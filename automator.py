#!/usr/bin/env python3

import time
import logging
import configparser
import threading
import os
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
logs_dir = os.path.join(script_dir, 'logs')
os.makedirs(logs_dir, exist_ok=True)

logging.basicConfig(
    filename=os.path.join(logs_dir, "automator.log"),
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

config_path = os.path.join(script_dir, 'automator.conf')
config = configparser.ConfigParser()
config.read(config_path)

scripts_dir = os.path.join(script_dir, 'scripts')
sys.path.append(scripts_dir)

def get_absolute_path(path):
    if not os.path.isabs(path):
        return os.path.normpath(os.path.join(script_dir, path))
    else:
        return os.path.normpath(path)

SLEEP_GET_CHANNELS = int(config.get('Automation', 'sleep_get_channels'))
SLEEP_AUTOFEE = int(config.get('Automation', 'sleep_autofee'))
SLEEP_GET_CLOSED_CHANNELS = int(config.get('Automation', 'sleep_get_closed_channels'))
SLEEP_REBALANCER = int(config.get('Automation', 'sleep_rebalancer'))
SLEEP_CLOSECHANNEL = int(config.get('Automation', 'sleep_closechannel'))

GET_CHANNELS_SCRIPT = get_absolute_path(config.get('Paths', 'get_channels_script'))
AUTO_FEE_SCRIPT = get_absolute_path(config.get('Paths', 'autofee_script'))
GET_CLOSED_CHANNELS_SCRIPT = get_absolute_path(config.get('Paths', 'get_closed_channels_script'))
REBALANCER_SCRIPT = get_absolute_path(config.get('Paths', 'rebalancer_script'))
CLOSE_CHANNEL_SCRIPT = get_absolute_path(config.get('Paths', 'close_channel_script'))
SWAP_OUT_SCRIPT = get_absolute_path(config.get('Paths', 'swap_out_script'))

ENABLE_AUTOFEE = config.getboolean('Control', 'enable_autofee')
ENABLE_GET_CLOSED_CHANNELS = config.getboolean('Control', 'enable_get_closed_channels')
ENABLE_REBALANCER = config.getboolean('Control', 'enable_rebalancer')
ENABLE_CLOSE_CHANNEL = config.getboolean('Control', 'enable_close_channel')
ENABLE_SWAP_OUT = config.getboolean('Control', 'enable_swap_out')

db_lock = threading.Lock()

def import_main_function(script_path):
    try:
        module_name = os.path.splitext(os.path.basename(script_path))[0]
        module = __import__(module_name)
        return module.main
    except Exception as e:
        logging.error(f"Erro ao importar a função 'main' de {script_path}: {e}")
        raise

def run_script_independently(main_function, sleep_time):
    while True:
        try:
            logging.info(f"Executando a função {main_function.__name__}")
            main_function()
            logging.info(f"{main_function.__name__} executada com sucesso")
        except Exception as e:
            logging.error(f"Erro ao executar {main_function.__name__}: {e}")
        time.sleep(sleep_time)

def run_get_channels(get_channels_main):
    while True:
        try:
            logging.info(f"Executando a função {get_channels_main.__name__}")
            get_channels_main()
            logging.info(f"{get_channels_main.__name__} executada com sucesso")
        except Exception as e:
            logging.error(f"Erro ao executar {get_channels_main.__name__}: {e}")
        time.sleep(SLEEP_GET_CHANNELS)

def run_autofee(autofee_main):
    while True:
        try:
            logging.info(f"Executando a função {autofee_main.__name__}")
            autofee_main()
            logging.info(f"{autofee_main.__name__} executada com sucesso")
        except Exception as e:
            logging.error(f"Erro ao executar {autofee_main.__name__}: {e}")
        time.sleep(SLEEP_AUTOFEE)

def run_swap_out(swap_out_main):
    try:
        logging.info(f"Executando a função {swap_out_main.__name__}")
        swap_out_main()
        logging.info(f"{swap_out_main.__name__} executada com sucesso")
    except Exception as e:
        logging.error(f"Erro ao executar {swap_out_main.__name__}: {e}")

def main():
    threads = []

    try:
        logging.info("Iniciando get_channels")
        get_channels_main = import_main_function(GET_CHANNELS_SCRIPT)
        thread1 = threading.Thread(target=run_get_channels, args=(get_channels_main,))
        threads.append(thread1)
        thread1.start()

        if ENABLE_AUTOFEE:
            logging.info("Iniciando autofee")
            autofee_main = import_main_function(AUTO_FEE_SCRIPT)
            thread2 = threading.Thread(target=run_autofee, args=(autofee_main,))
            threads.append(thread2)
            thread2.start()

        if ENABLE_GET_CLOSED_CHANNELS:
            logging.info("Iniciando get_closed_channels")
            get_closed_channels_main = import_main_function(GET_CLOSED_CHANNELS_SCRIPT)
            thread3 = threading.Thread(target=run_script_independently, args=(get_closed_channels_main, SLEEP_GET_CLOSED_CHANNELS))
            threads.append(thread3)
            thread3.start()

        if ENABLE_REBALANCER:
            logging.info("Iniciando rebalancer")
            rebalancer_main = import_main_function(REBALANCER_SCRIPT)
            thread4 = threading.Thread(target=run_script_independently, args=(rebalancer_main, SLEEP_REBALANCER))
            threads.append(thread4)
            thread4.start()

        if ENABLE_CLOSE_CHANNEL:
            logging.info("Iniciando close_channel")
            close_channel_main = import_main_function(CLOSE_CHANNEL_SCRIPT)
            thread5 = threading.Thread(target=run_script_independently, args=(close_channel_main, SLEEP_CLOSECHANNEL))
            threads.append(thread5)
            thread5.start()

        if ENABLE_SWAP_OUT:
            logging.info("Iniciando swap_out")
            swap_out_main = import_main_function(SWAP_OUT_SCRIPT)
            thread6 = threading.Thread(target=run_swap_out, args=(swap_out_main,))
            threads.append(thread6)
            thread6.start()

        for thread in threads:
            thread.join()

    except Exception as e:
        logging.error(f"Erro inesperado no controlador principal: {e}")
        raise

if __name__ == "__main__":
    logging.info("Iniciando o automator")
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Automator interrompido manualmente")
    except Exception as e:
        logging.error(f"Erro inesperado no automator: {e}")
    logging.info("Automator finalizado")
