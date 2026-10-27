"""Strict native tool selection; arbitrary provider prose never enters tool execution."""
import json
from .http import ServiceError


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def validate_identity(result, model):
    if not isinstance(result, dict):
        raise ServiceError('MODEL_SCHEMA_INVALID')
    actual = result.get('model')
    if not isinstance(actual, str) or actual.removeprefix('nvidia/') != model.removeprefix('nvidia/'):
        raise ServiceError('MODEL_IDENTITY_UNVERIFIED')
    metadata = result.get('meterless')
    route = metadata.get('winning_route') if isinstance(metadata, dict) else None
    if (not isinstance(route, dict) or route.get('provider') != 'nvidia'
            or not isinstance(route.get('model'), str)
            or route['model'].removeprefix('nvidia/') != model.removeprefix('nvidia/')):
        raise ServiceError('MODEL_PROVIDER_UNVERIFIED')


def parse_call(result, model, allowed):
    validate_identity(result, model)
    try:
        choices = result['choices']
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError
        choice = choices[0]
        message = choice['message']
        calls = message['tool_calls']
        if (choice['finish_reason'] != 'tool_calls' or message.get('refusal')
                or not isinstance(calls, list) or len(calls) != 1):
            raise ValueError
        call = calls[0]
        function = call['function']
        if call['type'] != 'function' or function['name'] not in allowed:
            raise ValueError
        if not isinstance(function['arguments'], str) or len(function['arguments'].encode()) > 4000:
            raise ValueError
        arguments = json.loads(function['arguments'], object_pairs_hook=_unique,
                               parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        if not isinstance(arguments, dict):
            raise ValueError
    except (KeyError, IndexError, AttributeError, TypeError, ValueError):
        raise ServiceError('MODEL_TOOL_CALL_INVALID') from None
    usage = result.get('usage')
    tokens = {k: v for k, v in usage.items() if k in (
        'prompt_tokens', 'completion_tokens', 'total_tokens') and type(v) is int and v >= 0
    } if isinstance(usage, dict) else {}
    return function['name'], arguments, tokens
