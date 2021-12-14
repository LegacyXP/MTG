import tensorflow as tf
import requests
import numpy as np
import pandas as pd
from matplotlib import colors
from matplotlib.colors import LinearSegmentedColormap
import matplotlib.pyplot as plt
import warnings

def print_deck(deck, cards, sort_by="name", return_str=False):
    cards = cards.sort_values(by=sort_by)
    output = ""
    for card_idx, card_name in cards[['idx','name']].to_numpy():
        count = deck[card_idx]
        if count > 0:
            print(count,card_name)
            if return_str:
                output += str(count) + " " + card_name + "\n"
    if return_str:
        return output
    
def print_counts(deck, cards, col="type_line"):
    col_counts = dict()
    cards = cards.set_index('idx')
    for card_idx, card_count in enumerate(deck):
        if card_count > 0:
            val = cards.loc[card_idx,col]
            if val in col_counts:
                col_counts[val] += card_count
            else:
                col_counts[val] = card_count
    for key, value in col_counts.items():
        print(key,":",value) 

def get_draft_json(draft_log_url):
    base_url = "https://www.17lands.com/data/draft?draft_id="
    draft_ext = draft_log_url.split("/")[-1].strip()
    log_json_url = base_url + draft_ext
    response = requests.get(log_json_url)
    return response.json()

def list_to_names(cards_json):
    if len(cards_json) > 0:
        return [x['name'].lower().split("//")[0].strip() for x in cards_json]
    else:
        return None

def names_to_array(names, mapping):
    names = list_to_names(names)
    idxs = [mapping[name] for name in names]
    unique, counts = np.unique(idxs, return_counts=True)
    arr = np.zeros(len(mapping))
    arr[unique] += counts
    return arr

def load_arena_ids(expansion):
    arena_id_file = '/content/drive/My Drive/mtg_data/card_list.csv'
    id_df = pd.read_csv(arena_id_file)
    id_df = id_df[(id_df['expansion'] == expansion) & (id_df['is_booster'])]
    id_df['name'] = id_df['name'].str.lower()
    return id_df.set_index('name')['id'].to_dict()

def names_to_arena_ids(names, expansion='VOW', mapping=None, return_mapping=False):
    if mapping is None:
        mapping = load_arena_ids(expansion)
    if not isinstance(names, list):
        names = [names]
    output = [mapping[x['name'].lower().split("//")[0].strip()] for x in names]
    if return_mapping:
        output = (output, mapping)
    return output

def draft_log_ai(draft_log_url, model, t=None, n_cards=None, idx_to_name=None, return_attention=False, return_style='df', batch_size=1, exchange_picks=-1, exchange_packs=-1, return_model_input=False, token=""):
    exchange_picks = [exchange_picks] if isinstance(exchange_picks, int) else exchange_picks
    exchange_packs = [exchange_packs] if isinstance(exchange_packs, int) else exchange_packs
    name_to_idx = {v:k for k,v in idx_to_name.items()}
    picks = get_draft_json(draft_log_url)['picks']
    n_picks_per_pack = t/3
    n_cards = len(name_to_idx)
    pool = np.zeros(n_cards)
    draft_info = np.zeros((batch_size, t, n_cards * 2))
    positions = np.tile(np.expand_dims(np.arange(t, dtype=np.int32),0),batch_size).reshape(batch_size,t)
    actual_pick = []
    position_to_pxpy = dict()
    js = {
        "expansion":"VOW",
        "token":f"{token}",
        "picks":[]
    }
    arena_id_mapping = None
    for pick in picks:
        arena_ids_in_pack, arena_id_mapping = names_to_arena_ids(pick['available'], mapping=arena_id_mapping, return_mapping=True)
        pick_js = {
            "pack_number":pick['pack_number'],
            "pick_number":pick['pick_number'],
            "pack_cards": arena_ids_in_pack,
            "pick":-1
        }
        js['picks'].append(pick_js)
        if pick['pick_number'] in exchange_picks:
            exchange = True
        else:
            exchange = False
        position = int(pick['pack_number'] * n_picks_per_pack + pick['pick_number'])
        if exchange and pick['pack_number'] in exchange_packs:
            correct_pick_options = [x['name'].lower().split("//")[0].strip() for x in pick['available'] if x['name'] != pick['pick']['name']]
            correct_pick = np.random.choice(correct_pick_options)
            position_to_pxpy[position] = "P" + str(pick['pack_number'] + 1) + "P*" + str(pick['pick_number'] + 1)
        else:
            correct_pick = pick['pick']['name'].lower().split("//")[0].strip()
            position_to_pxpy[position] = "P" + str(pick['pack_number'] + 1) + "P" + str(pick['pick_number'] + 1)
        pick_idx = name_to_idx[correct_pick]
        pack = names_to_array(pick['available'], name_to_idx)
        draft_info[0, position, :n_cards] = pack
        draft_info[0, position, n_cards:] = pool
        pool[pick_idx] += 1
        actual_pick.append(correct_pick)
    #insert n_cards idx to shift the picks passed into the model to prevent seeing the correct pick
    np_pick = np.tile(np.expand_dims(np.asarray([n_cards] + [name_to_idx[name] for name in actual_pick[:-1]]), 0),batch_size).reshape(batch_size,42)
    model_input = (
        tf.convert_to_tensor(draft_info, dtype=tf.float32),
        tf.convert_to_tensor(np_pick, dtype=tf.int32),
        tf.convert_to_tensor(positions, dtype=tf.int32)
    )
    if return_style=='input':
        return model_input
    # we get the first element in anything we return to handle the case where the model couldn't properly serialize
    # and we hence need to copy the data to be the same shape as the batch size in order to run a stored model
    if return_attention:
        output, attention = model(model_input, training=False, return_attention=True)
        output = output[0]
        attention = (attention[0][0], attention[1][0])
        #attention = tf.squeeze(attention)
    else:
        output = model(model_input, training=False)[0]
    if return_style=='output':
        if return_attention:
            return output, attention
        else:
            return output
    predictions = tf.math.top_k(output, k=3).indices.numpy()
    predicted_picks = [idx_to_name[pred[0]] for pred in predictions]
    if return_style == 'df':
        df = pd.DataFrame()
        df['predicted_pick'] = predicted_picks
        df['human_pick'] = actual_pick
        df['second_choice'] = [idx_to_name[pred[1]] for pred in predictions]
        df['second_choice'].loc[
            [idx for idx in df.index if idx % n_picks_per_pack >= n_picks_per_pack - 1]
        ] = ''
        df['third_choice'] = [idx_to_name[pred[2]] for pred in predictions]
        df['third_choice'].loc[
            [idx for idx in df.index if idx % n_picks_per_pack >= n_picks_per_pack - 2]
        ] = ''
        df.index = [position_to_pxpy[idx] for idx in df.index]
        if return_attention:
            return df, attention
        return df
    for i,js_obj in enumerate(js['picks']):
        js_obj['pick'] = arena_id_mapping[predicted_picks[i]]
    r = requests.post(url = "https://www.17lands.com/api/submit_draft", json = js)
    r_js = r.json()
    try:
        draft_id = r_js['id']
        return f"https://www.17lands.com/submitted_draft/{draft_id}"
    except:
        warnings.warn("Draft Log Upload Failed. Returning sent JSON to help debug.")
        return (js, r)

def display_draft(df, cmap=None, pack=None):
    if pack is not None:
        df = df.loc[[x for x in df.index if x.startswith("P" + str(pack))]]
    if cmap is None:
        cmap=LinearSegmentedColormap.from_list('gr',["g", "w", "r"], N=256)
    cm = plt.cm.get_cmap(cmap)
    good_c = colors.rgb2hex(cm(int(cmap.N * 1/3)))
    bad_c = colors.rgb2hex(cm(int(cmap.N * 2/3)))
    human_picks = df['human_pick'].values
    anything_correct = np.zeros_like(human_picks)
    def f(dat, good_c='green', bad_c='red', human_col_val=None):
        output = []
        for i,pick in enumerate(dat):
            if human_col_val is not None:
                flag = pick == human_picks[i]
            else:
                flag = anything_correct[i]
                good_c = colors.rgb2hex(cm(int(cmap.N * (1- flag))))
            if flag:
                output.append(f'background-color: {good_c}')
                if human_col_val is not None:
                    anything_correct[i] += human_col_val
            else:
                output.append(f'background-color: {bad_c}')
        return output
    style = df.style
    human_col_val_map = {
        'predicted_pick': 1.0,
        'second_choice': 2.0/3.0,
        'third_choice': 2.0/3.0
    }
    for column in df.columns:
        if column == "human_pick":
            continue
        style = style.apply(f, axis=0, subset=column, good_c=good_c, bad_c=bad_c, human_col_val=human_col_val_map[column])
    style = style.apply(f, axis=0, subset="human_pick", good_c=good_c, bad_c=bad_c)
    return style.set_properties(**{'text-align': 'center', 'padding':"10px", 'border':'1px solid black', 'margin':'0px'})

def plot_attention_head(attention, pxpy):

    ax = plt.gca()
    ax.matshow(attention)
    ax.set_xticks(range(len(pxpy)))
    ax.set_yticks(range(len(pxpy)))

    ax.set_xticklabels(
        pxpy, rotation=90)

    ax.set_yticklabels(pxpy)

def plot_attention_weights(attention_heads):
  #pxpy = ["BIAS"]
  pxpy = []
  seq_l = attention_heads.shape[-1]
  n_picks = (seq_l)/3
  for i in range(seq_l):
      pack = i//n_picks + 1
      pick = (i % n_picks) + 1
      pxpy.append("P" + str(int(pack)) + "P" + str(int(pick)))

  for h, head in enumerate(attention_heads):
    fig = plt.figure(figsize=(10,30))
    plot_attention_head(head, pxpy)
    plt.scatter(range(seq_l),range(seq_l), color="red")
    plt.grid()
    plt.title(f'Head {h+1}')
    plt.tight_layout()
    plt.show()