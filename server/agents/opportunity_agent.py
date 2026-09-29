from uuid import uuid4
from server.demo_seed import example
from server.schemas import Mission

async def demo_opportunity(state):
    mission = example('mission')
    mission.update(id=f'mission-{uuid4().hex[:12]}', context_id=state['context']['id'])
    return Mission.model_validate(mission)

PROMPT = 'Choose one safe, short practice mission matching the context and a skill with high knowledge but low application. Return only the Mission contract.'
