from server.demo_seed import example
from server.schemas import ReflectionResult

async def demo_reflection(mission, recording_id, transcript):
    # Fixed fixture, not transcript analysis. The backend labels this as simulation.
    result = example('reflection')
    result.update(mission_id=mission['id'], recording_id=recording_id)
    return ReflectionResult.model_validate(result)

PROMPT = 'Analyze only supplied transcript evidence. Return the ReflectionResult contract. Never calculate application scores or execute database queries.'
