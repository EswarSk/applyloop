"""Structured model calls shared by the two BAND remote agents."""
import json
import os


def strict_schema(schema):
    # OpenAI strict outputs require every property, including defaulted fields.
    if isinstance(schema, dict):
        if schema.get('type') == 'object':
            schema['required'] = list(schema.get('properties', {}))
            schema['additionalProperties'] = False
        schema.pop('default', None)
        for value in schema.values():
            strict_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            strict_schema(value)
    return schema


async def generate(client, prompt, payload, contract, *, schema=None):
    response = await client.post('https://api.openai.com/v1/chat/completions',
        headers={'Authorization': f'Bearer {os.environ["OPENAI_API_KEY"]}'},
        json={'model': os.getenv('OPENAI_MODEL', 'gpt-4o-mini'),
              'messages': [{'role': 'system', 'content': prompt},
                           {'role': 'user', 'content': json.dumps(payload)}],
              'response_format': {'type': 'json_schema', 'json_schema': {
                  'name': contract.__name__, 'strict': True,
                  'schema': strict_schema(schema if schema is not None else contract.model_json_schema())}}})
    if response.is_error:
        # Don't echo provider responses, which can contain private inputs.
        raise RuntimeError(f'Model request failed (HTTP {response.status_code})')
    message = response.json()['choices'][0]['message']
    if message.get('refusal') or not message.get('content'):
        raise ValueError('Model refused or returned an empty result')
    return contract.model_validate_json(message['content'])
