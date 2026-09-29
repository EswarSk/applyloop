from server.demo_seed import example
from server.learning_rules import curriculum
from server.schemas import ReflectionResult

async def demo_reflection(mission, recording_id, transcript):
    # Fixed fixture per activity (keyed by the mission's skill), not transcript analysis. Labelled as simulation.
    replay = curriculum()['demo_replays'].get(mission['skill_id'])
    result = dict(replay['reflection']) if replay else example('reflection')
    result.update(mission_id=mission['id'], recording_id=recording_id)
    return ReflectionResult.model_validate(result)

def demo_transcript(skill_id, default):
    replay = curriculum()['demo_replays'].get(skill_id)
    return replay['transcript'] if replay else default

PROMPT = ('Analyze only supplied transcript evidence. Return the ReflectionResult contract, including word_evidence '
          '(word_id from the supplied curriculum word list; outcome used_correctly | used_incorrectly | not_understood). '
          'Never calculate application scores, word statuses or levels, and never execute database queries.')
