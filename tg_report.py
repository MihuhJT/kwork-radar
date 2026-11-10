"""Minimal Telegram notification helpers."""
from pathlib import Path

import requests


def load_env():
    env_file = Path(__file__).parent / '.env'
    env = {}
    for line in env_file.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip()
    return env


def api(token, method, **params):
    response = requests.post(
        f'https://api.telegram.org/bot{token}/{method}', data=params, timeout=40
    )
    response.raise_for_status()
    result = response.json()
    if not result.get('ok'):
        raise RuntimeError('Telegram API rejected the request')
    return result
