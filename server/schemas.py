from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Text = Annotated[str, Field(min_length=1, max_length=2000)]
Identifier = Annotated[str, Field(min_length=1, max_length=200)]

class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class ContextInput(Payload):
    title: Text
    type: Text
    location: Text

class Mission(Payload):
    id: Identifier
    skill_id: Identifier
    context_id: Identifier
    title: Text
    challenge: Text
    reason: Text
    confidence: Score
    status: Literal['assigned', 'completed'] = 'assigned'

class Demonstration(Payload):
    skill_id: Identifier
    evidence: Text
    confidence: Score

class Gap(Demonstration):
    name: Text

class NextTarget(Payload):
    skill_id: Identifier
    reason: Text

class ReflectionResult(Payload):
    mission_id: Identifier
    recording_id: Identifier
    success_score: Score
    demonstrated: list[Demonstration] = Field(max_length=20)
    gaps: list[Gap] = Field(max_length=20)
    next_target: NextTarget

class RecordingInput(Payload):
    recording_id: Identifier
    title: Text

class TranscriptInput(Payload):
    recording_id: Identifier
    transcript: Annotated[str, Field(min_length=10, max_length=100000)]

class BridgeStatusInput(Payload):
    status: Literal['waiting', 'transcript_waiting', 'error']
    message: Text
