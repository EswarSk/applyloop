import json
from server.settings import ROOT

SAMPLE_TRANSCRIPT = ('I ordered my food and asked for water in Spanish. But when the waiter '
                     'asked whether I wanted red or green salsa, I could not understand '
                     'the question and switched to English.')

def example(name):
    return json.loads((ROOT / 'contracts' / 'examples' / f'{name}.json').read_text())
