import json
from server.settings import ROOT

# Prerecorded demonstration: quoted Spanish supports live evidence analysis.
SAMPLE_TRANSCRIPT = ('I ordered my food and asked for water in Spanish. But when the waiter '
                     'asked whether I wanted red or green salsa, I could not understand '
                     'the question and switched to English.\n'
                     'Learner: Hola, quisiera dos tacos y agua, por favor. ¿Qué recomienda? '
                     'Waiter: Recomiendo los tacos de pollo. ¿Salsa roja o verde? '
                     'Learner: No entiendo. ¿Puede repetir más despacio, por favor? '
                     'Waiter: ¿Quiere salsa roja o verde? '
                     'Learner: Verde, gracias. ¿Cuánto cuesta? ¿La cuenta, por favor?')

def example(name):
    return json.loads((ROOT / 'contracts' / 'examples' / f'{name}.json').read_text())
