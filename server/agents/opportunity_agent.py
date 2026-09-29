from uuid import uuid4
from server.demo_seed import example
from server.learning_rules import curriculum, scenario_skill
from server.schemas import Mission

async def demo_opportunity(state):
    """Fixture mission for the learner's current activity (restaurant, Spanish class, dance class)."""
    context = state['context']
    skill_id = scenario_skill(context.get('type')) or 'restaurant-ordering'
    template = curriculum()['demo_missions'].get(skill_id)
    mission = example('mission')
    if template:
        mission.update(skill_id=skill_id, confidence=0.9, **template)
    mission.update(id=f'mission-{uuid4().hex[:12]}', context_id=context['id'])
    focus = state.get('focus_words') or []
    if focus:
        # Words the graph says you learned but have not yet used fluently in real life, needed in this context.
        mission['challenge'] += ' Try to use: ' + ', '.join(w['lemma'] for w in focus) + '.'
    return Mission.model_validate(mission)

PROMPT = ('Choose one safe, short practice mission for the learner\'s current activity (state.context, e.g. a Spanish class, '
          'a restaurant or a dance class) and a skill with high knowledge but low application. Weave in state.focus_words '
          '(learned, not yet used fluently). Return only the Mission contract.')
