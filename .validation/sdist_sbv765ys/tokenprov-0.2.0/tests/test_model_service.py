"""The GPU service rejects unsupported work before it enters the GPU queue."""
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location('model_service',
    Path(__file__).resolve().parents[1] / 'scripts' / 'serve_transformers.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class Tokenizer:
    def apply_chat_template(self, messages, **kwargs):
        return [1, 2, 3, 4]


def payload(**changes):
    return {'model': 'local', 'messages': [{'role': 'user', 'content': 'hello'}],
        'max_tokens': 8, **changes}


def test_context_limit_uses_actual_tokenized_prompt_plus_output_cap():
    ids, limit = service.encode_request(payload(), Tokenizer(), 'local', 12)
    assert len(ids) == 4 and limit == 8
    with pytest.raises(ValueError, match='context limit'):
        service.encode_request(payload(), Tokenizer(), 'local', 11)


@pytest.mark.parametrize('changes', [
    {'stream': True}, {'temperature': 0.5}, {'max_tokens': True},
    {'max_tokens': 0}, {'model': 'other'}, {'messages': []},
    {'messages': [{'role': 'user', 'content': {'image': 'unsupported'}}]},
    {'chat_template_kwargs': {'enable_thinking': True}},
])
def test_unsupported_requests_are_rejected(changes):
    with pytest.raises(ValueError):
        service.encode_request(payload(**changes), Tokenizer(), 'local', 4096)


def test_server_help_does_not_require_torch_or_model_files(capsys):
    with pytest.raises(SystemExit) as result:
        service.parse_args(['--help'])
    assert result.value.code == 0
    assert '--model-dir' in capsys.readouterr().out
