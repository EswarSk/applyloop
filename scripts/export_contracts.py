import json
from server.schemas import ContextInput, Mission, ReflectionResult, RecordingInput, TranscriptInput
from server.settings import ROOT

if __name__ == '__main__':
    models = [ContextInput, Mission, ReflectionResult, RecordingInput, TranscriptInput]
    (ROOT / 'contracts/schemas.json').write_text(json.dumps({m.__name__: m.model_json_schema() for m in models}, indent=2) + '\n')
