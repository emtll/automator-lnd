import json
import os
import configparser
import sqlite3
from datetime import datetime, timedelta

config_file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'automator.conf'))
config = configparser.ConfigParser()
config.read(config_file_path)

def expand_path(path):
    if not os.path.isabs(path):
        return os.path.join(os.path.expanduser("~"), path)
    return os.path.expanduser(path)

LNDG_DB_PATH = expand_path(config['Paths']['lndg_db_path'])
BOS_PATH = expand_path(config['Paths']['bos_path'])
DB_PATH = expand_path(config['Paths']['db_path'])
EXCLUSION_FILE_PATH = expand_path(config['Paths']['excluded_peers_path'])
SLEEP_AUTOFEE = int(config['Automation']['sleep_autofee'])
MAX_FEE_THRESHOLD = int(config['Autofee']['max_fee_threshold'])
PERIOD = config['Autofee']['table_period']
conn = sqlite3.connect(DB_PATH)

def print_with_timestamp(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")

def issue_bos_command(peer_pubkey, update_fee):
    command = f"{BOS_PATH} fees --set-fee-rate {update_fee} --to {peer_pubkey}"
    print_with_timestamp(f"Executing: {command}")
    os.system(command)

def days_since_last_activity(last_activity):
    if last_activity is None or last_activity == '':
        return float('inf')
    if isinstance(last_activity, int):
        last_activity_date = datetime.fromtimestamp(last_activity)
    else:
        last_activity_date = datetime.strptime(last_activity, '%Y-%m-%d %H:%M:%S')
    return (datetime.now() - last_activity_date).days

def calculate_new_fee(total_cost_ppm):
    return int(total_cost_ppm / 0.8)  # Adds 20% margin

def is_excluded(pubkey, exclusion_list):
    return pubkey in [entry['pubkey'] for entry in exclusion_list]

def fee_change_checker(chan_id):
    conn_lndg = sqlite3.connect(LNDG_DB_PATH, timeout=30)
    cursor = conn_lndg.cursor()
    time_limit = datetime.now() - timedelta(seconds=SLEEP_AUTOFEE)
    
    cursor.execute("""
        SELECT timestamp FROM gui_autofees
        WHERE chan_id = ? AND timestamp >= ?
        ORDER BY timestamp DESC LIMIT 1
    """, (chan_id, time_limit.strftime('%Y-%m-%d %H:%M:%S')))
    
    result = cursor.fetchone()
    conn_lndg.close()
    
    return result is not None

def adjust_inbound_fee(channel, new_fee, local_fee_rate, rebal_rate, peer_pubkey):
    current_fee = new_fee if new_fee != local_fee_rate else local_fee_rate
    projected_margin = current_fee - rebal_rate

    if projected_margin > 0:
        if channel['tag'] == 'sink':
            inbound_fee = int(projected_margin * 0.25)
        elif channel['tag'] == 'router':
            inbound_fee = int(projected_margin * 0.10)
        else:
            inbound_fee = 0

        print_with_timestamp(f"Setting inbound fee for channel {channel['alias']} ({peer_pubkey}) to {inbound_fee}")
        command = f"{BOS_PATH} fees --set-inbound-rate-discount {inbound_fee} --to {peer_pubkey}"
        print_with_timestamp(f"Executing: {command}")
        os.system(command)
    else:
        inbound_fee = 0
        command = f"{BOS_PATH} fees --set-inbound-rate-discount {inbound_fee} --to {peer_pubkey}"
        print_with_timestamp(f"No projected profit margin for channel {channel['alias']} ({peer_pubkey}), inbound fee droped to 0")
        print_with_timestamp(f"{command}")
        os.system(command)

def get_routed_amount_7_days(chan_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT SUM(total_routed_in), SUM(total_routed_out)
        FROM opened_channels_7d
        WHERE chan_id = ?
    """, (chan_id,))
    
    result = cursor.fetchone()
    total_routed_in = result[0] if result[0] is not None else 0
    total_routed_out = result[1] if result[1] is not None else 0
    return total_routed_in + total_routed_out

def adjust_new_channel_fee(channel):
    outbound_ratio = channel['outbound_liquidity']      # outbound_liquidity
    days_since_opening = channel['days_open']           # days_open
    local_fee_rate = channel['local_fee_rate']          # local_fee_rate
    last_outgoing = channel['last_outgoing_activity']   # last_outgoing_activity
    last_incoming = channel['last_incoming_activity']   # last_incoming_activity
    last_rebalance = channel['last_rebalance']          # last_rebalance

    if days_since_opening >= 1 and outbound_ratio == 0 and last_incoming is None and last_rebalance is None:
        return int(local_fee_rate * 1.10)  # Fee Increase 10%
    elif outbound_ratio >= 99 and days_since_opening >= 1 and last_outgoing is None:
        return int(local_fee_rate * 0.95)  # Fee Decrease 5%
    elif outbound_ratio == 50 and days_since_opening >= 1 and last_outgoing is None:
        return int(local_fee_rate * 0.95)  # Fee Decrease 5%

    return local_fee_rate  # No Update

def adjust_sink_fee(channel):
    outbound_ratio = channel['outbound_liquidity']      # outbound_liquidity
    total_cost_ppm = channel['cost_ppm']                # cost_ppm
    local_fee_rate = channel['local_fee_rate']          # local_fee_rate
    last_outgoing = channel['last_outgoing_activity']   # last_outgoing_activity
    last_rebalance = channel['last_rebalance']          # last_rebalance
    rebal_rate = channel['rebal_rate']                  # rebal_rate

    if total_cost_ppm == 0 and days_since_last_activity(last_rebalance) <= 21:
        return 100  # Set Fee Rate to 100ppm 
    elif rebal_rate > 0 and rebal_rate < 100 and days_since_last_activity(last_rebalance) <= 21:
        return int(rebal_rate * 2) # Set Fee Rate to 2x the rebal_rate
    elif last_rebalance is not None and days_since_last_activity(last_rebalance) > 21:
        return 2500 # Set Fee Rate to 2500ppm
    elif outbound_ratio <= 10.0 and days_since_last_activity(last_rebalance) >= 2 and local_fee_rate < MAX_FEE_THRESHOLD:
        return int(local_fee_rate * 1.05)  # Fee Increase 5%
    elif outbound_ratio <= 10.0 and days_since_last_activity(last_rebalance) > 1 and local_fee_rate < MAX_FEE_THRESHOLD:
        return int(local_fee_rate * 1.03)  # Fee Increase 3%
    elif outbound_ratio >= 10.0 and outbound_ratio < 30.0 and days_since_last_activity(last_rebalance) >= 2:
        return int(local_fee_rate * 1.02)  # Fee Increase 2%
    elif outbound_ratio >= 10.0 and outbound_ratio < 30.0 and days_since_last_activity(last_outgoing) >= 1:
        new_fee = int(local_fee_rate * 0.98)  # Fee Decrease 2%
        if new_fee > rebal_rate:
            return new_fee
        else:
            return local_fee_rate # Maintains the same fee
    elif outbound_ratio >= 30.0 and days_since_last_activity(last_outgoing) >= 1:
        new_fee = int(local_fee_rate * 0.98)  # Fee Decrease 2%
        if new_fee > total_cost_ppm:
            return new_fee # The new fee always must be greater than the total cost ppm
        else:
            return local_fee_rate # Maintains the same fee
    else:
        return 500 if rebal_rate == 0 else int(rebal_rate / 0.9)  # Rebal Rate + 10%
        
def adjust_router_fee(channel):
    outbound_ratio = channel['outbound_liquidity']      # outbound_liquidity
    total_cost_ppm = channel['cost_ppm']                # cost_ppm
    local_fee_rate = channel['local_fee_rate']          # local_fee_rate
    last_outgoing = channel['last_outgoing_activity']   # last_outgoing_activity
    last_incoming = channel['last_incoming_activity']   # last_incoming_activity
    last_rebalance = channel['last_rebalance']          # last_rebalance
    rebal_rate = channel['rebal_rate']                  # rebal_rate
    channel_capacity = channel['capacity']              # total capacity of the channel
    routed_amount = get_routed_amount_7_days(channel['chan_id']) 

    # If there was intense movement (2x channel capacity) and fee rate is below 100ppm, set it to 100
    if total_cost_ppm == 0 and outbound_ratio > 10:
        return 100  # Set Fee Rate to 100ppm 
    elif total_cost_ppm > 0 and total_cost_ppm < 100:
        return int(total_cost_ppm * 2) # Set Fee Rate to 2x the total_cost_ppm
    elif days_since_last_activity(last_rebalance) > 21 and outbound_ratio <= 10:
        return int(local_fee_rate * 1.5) # Set Fee Rate to 1.5x the local_fee_rate
    elif outbound_ratio <= 10.0 and days_since_last_activity(last_rebalance) >= 1 and local_fee_rate < MAX_FEE_THRESHOLD:
        return int(local_fee_rate * 1.03)  # Fee Increase 3%
    elif outbound_ratio <= 10.0 and days_since_last_activity(last_rebalance) < 1 and local_fee_rate < MAX_FEE_THRESHOLD:
        return int(local_fee_rate * 1.02)  # Fee Increase 2%
    elif outbound_ratio >= 10.0 and outbound_ratio < 30.0 and days_since_last_activity(last_rebalance) >= 3:
        return int(local_fee_rate * 1.01)  # Fee Increase 1%
    elif outbound_ratio >= 10.0 and outbound_ratio < 30.0 and days_since_last_activity(last_outgoing) >= 1:
        new_fee = int(local_fee_rate * 0.98)  # Fee Decrease 2%
        if new_fee > rebal_rate:
            return new_fee  # The new fee always must be greater than the rebal_rate
        else:
            return local_fee_rate  # Maintains the same fee
    elif outbound_ratio >= 30.0 and days_since_last_activity(last_outgoing) >= 1:
        new_fee = int(local_fee_rate * 0.98)  # Fee Decrease 2%
        if new_fee > total_cost_ppm:
            return new_fee  # The new fee always must be greater than the total cost ppm
        else:
            return local_fee_rate  # Maintains the same fee
    elif routed_amount >= 2 * channel_capacity and local_fee_rate < 100:
        return 100  # Set Fee Rate to 100ppm
    else:
        return 100 if total_cost_ppm == 0 else int(total_cost_ppm / 0.9) # Ensure minimum of 100ppm

def adjust_source_fee(channel):
    total_routed_out = channel['total_routed_out']   # total_routed_out
    if total_routed_out > 0:
        return 10  # Set Fee Rate to 10ppm
    else:
        return 0  # Set Fee Rate to 0ppm

def main():
    with open(EXCLUSION_FILE_PATH, 'r') as exclusion_file:
        exclusion_data = json.load(exclusion_file)
        exclusion_list = exclusion_data.get('EXCLUSION_LIST', [])

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    table_name = f'opened_channels_{PERIOD}d'
    
    try:
        cursor.execute(f"SELECT * FROM {table_name}")
    except sqlite3.Error as e:
        print_with_timestamp(f"Database error: {e}")
        return
    channels_data = cursor.fetchall()
    column_names = [description[0] for description in cursor.description]

    for channel in channels_data:
        channel_dict = dict(zip(column_names, channel))

        chan_id = channel_dict.get('chan_id', None)
        pubkey = channel_dict.get('pubkey', None)
        alias = channel_dict.get('alias', None)
        tag = channel_dict.get('tag', None)
        local_fee_rate = channel_dict.get('local_fee_rate', None)
        rebal_rate = channel_dict.get('rebal_rate', 0)

        if chan_id is None or pubkey is None or alias is None or tag is None:
            print_with_timestamp(f"Missing required data for channel, skipping...")
            continue

        if is_excluded(pubkey, exclusion_list):
            print_with_timestamp(f"Channel {alias} ({pubkey}) is in the exclusion list, skipping...")
            continue

        if fee_change_checker(chan_id):
            print_with_timestamp(f"Channel {alias} ({pubkey}) had a recent fee change, skipping...")
            continue

        if tag == "new_channel":
            new_fee = adjust_new_channel_fee(channel_dict)
        elif tag == "sink":
            new_fee = adjust_sink_fee(channel_dict)
            #adjust_inbound_fee(channel_dict, new_fee, local_fee_rate, rebal_rate, pubkey)
        elif tag == "router":
            new_fee = adjust_router_fee(channel_dict)
            #adjust_inbound_fee(channel_dict, new_fee, local_fee_rate, rebal_rate, pubkey)
        elif tag == "source":
            new_fee = adjust_source_fee(channel_dict)
        else:
            print_with_timestamp(f"Unknown tag for {alias}, skipping...")
            continue

        if new_fee != local_fee_rate and new_fee > (local_fee_rate*1.005):
            print_with_timestamp(f"Adjusting fee for channel {alias} ({pubkey}) to {new_fee}")
            issue_bos_command(pubkey, new_fee)

    conn.close()

if __name__ == "__main__":
    main()
