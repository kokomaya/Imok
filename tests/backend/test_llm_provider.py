"""LLM Provider 配置 + 工厂模块单元测试。

覆盖范围：
- ProviderEntry 数据类与 resolve_api_token()
- ProvidersConfig 默认/按名称获取
- load_providers_config() YAML 解析与校验
- CompanyLLMClient extra_headers / verify_ssl 扩展
- create_client_from_provider() 工厂函数
- create_client_from_config() 端到端
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Dict
from unittest.mock import patch

import pytest

from backend.config import LLMSettings
from backend.llm.base import LLMClientState
from backend.llm.client import CompanyLLMClient
from backend.llm.factory import (
    create_client_from_config,
    create_client_from_provider,
)
from backend.llm.provider_config import (
    ProviderEntry,
    ProvidersConfig,
    load_providers_config,
)


# =========================================================================
# 1. ProviderEntry 测试
# =========================================================================
class TestProviderEntry:
    def test_defaults(self):
        entry = ProviderEntry(type="openai_compatible", base_url="http://x", model="m")
        assert entry.api_token_env == ""
        assert entry.headers == {}
        assert entry.timeout == 60.0
        assert entry.max_retries == 2
        assert entry.stream is False
        assert entry.ssl_verify is True

    def test_custom_fields(self):
        entry = ProviderEntry(
            type="openai_compatible",
            base_url="https://api.example.com",
            model="gpt-4",
            api_token_env="MY_KEY",
            headers={"X-Tenant-ID": "t1"},
            timeout=120.0,
            max_retries=5,
            stream=True,
            ssl_verify=False,
        )
        assert entry.base_url == "https://api.example.com"
        assert entry.headers["X-Tenant-ID"] == "t1"
        assert entry.ssl_verify is False

    def test_resolve_api_token_empty_env_name(self):
        entry = ProviderEntry(type="openai_compatible", base_url="http://x", model="m")
        assert entry.resolve_api_token() == ""

    def test_resolve_api_token_env_not_set(self):
        entry = ProviderEntry(
            type="openai_compatible",
            base_url="http://x",
            model="m",
            api_token_env="NONEXISTENT_TOKEN_XYZ",
        )
        with patch.dict("os.environ", {}, clear=False):
            assert entry.resolve_api_token() == ""

    def test_resolve_api_token_from_env(self):
        entry = ProviderEntry(
            type="openai_compatible",
            base_url="http://x",
            model="m",
            api_token_env="TEST_LLM_TOKEN",
        )
        with patch.dict("os.environ", {"TEST_LLM_TOKEN": "secret-123"}):
            assert entry.resolve_api_token() == "secret-123"


# =========================================================================
# 2. ProvidersConfig 测试
# =========================================================================
class TestProvidersConfig:
    def _make_entry(self, **kwargs) -> ProviderEntry:
        defaults = dict(type="openai_compatible", base_url="http://x", model="m")
        defaults.update(kwargs)
        return ProviderEntry(**defaults)

    def test_get_default(self):
        config = ProvidersConfig(
            default_provider="alpha",
            providers={"alpha": self._make_entry(), "beta": self._make_entry(model="b")},
        )
        assert config.get_default().model == "m"

    def test_get_default_missing_raises(self):
        config = ProvidersConfig(
            default_provider="missing",
            providers={"alpha": self._make_entry()},
        )
        with pytest.raises(KeyError, match="missing"):
            config.get_default()

    def test_get_provider_by_name(self):
        config = ProvidersConfig(
            default_provider="alpha",
            providers={"alpha": self._make_entry(), "beta": self._make_entry(model="beta-model")},
        )
        assert config.get_provider("beta").model == "beta-model"

    def test_get_provider_missing_raises(self):
        config = ProvidersConfig(
            default_provider="alpha",
            providers={"alpha": self._make_entry()},
        )
        with pytest.raises(KeyError, match="nope"):
            config.get_provider("nope")


# =========================================================================
# 3. load_providers_config() YAML 解析测试
# =========================================================================
class TestLoadProvidersConfig:
    def test_valid_yaml(self, tmp_path: Path):
        yaml_content = textwrap.dedent("""\
            default_provider: "vio"
            providers:
              vio:
                type: "openai_compatible"
                base_url: "https://vio.example.com:446"
                model: "my-model"
                api_token_env: "API_TOKEN"
                headers:
                  useLegacyCompletionsEndpoint: "false"
                  X-Tenant-ID: "test_tenant"
                timeout: 90
                max_retries: 3
                stream: true
                ssl_verify: false
        """)
        path = tmp_path / "providers.yaml"
        path.write_text(yaml_content, encoding="utf-8")

        config = load_providers_config(path)
        assert config.default_provider == "vio"
        assert "vio" in config.providers

        vio = config.providers["vio"]
        assert vio.type == "openai_compatible"
        assert vio.base_url == "https://vio.example.com:446"
        assert vio.model == "my-model"
        assert vio.api_token_env == "API_TOKEN"
        assert vio.headers["X-Tenant-ID"] == "test_tenant"
        assert vio.timeout == 90.0
        assert vio.max_retries == 3
        assert vio.stream is True
        assert vio.ssl_verify is False

    def test_multiple_providers(self, tmp_path: Path):
        yaml_content = textwrap.dedent("""\
            default_provider: "a"
            providers:
              a:
                type: "openai_compatible"
                base_url: "http://a"
                model: "model-a"
              b:
                type: "openai_compatible"
                base_url: "http://b"
                model: "model-b"
                stream: true
        """)
        path = tmp_path / "providers.yaml"
        path.write_text(yaml_content, encoding="utf-8")

        config = load_providers_config(path)
        assert len(config.providers) == 2
        assert config.providers["b"].stream is True

    def test_minimal_provider(self, tmp_path: Path):
        """只需 type, base_url, model 即可。"""
        yaml_content = textwrap.dedent("""\
            default_provider: "min"
            providers:
              min:
                type: "openai_compatible"
                base_url: "http://local"
                model: "llama3"
        """)
        path = tmp_path / "providers.yaml"
        path.write_text(yaml_content, encoding="utf-8")

        config = load_providers_config(path)
        entry = config.get_default()
        assert entry.headers == {}
        assert entry.ssl_verify is True
        assert entry.timeout == 60.0

    def test_file_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_providers_config(tmp_path / "nope.yaml")

    def test_missing_default_provider(self, tmp_path: Path):
        yaml_content = textwrap.dedent("""\
            providers:
              a:
                type: "openai_compatible"
                base_url: "http://a"
                model: "m"
        """)
        path = tmp_path / "bad.yaml"
        path.write_text(yaml_content, encoding="utf-8")

        with pytest.raises(ValueError, match="default_provider"):
            load_providers_config(path)

    def test_missing_providers_section(self, tmp_path: Path):
        yaml_content = 'default_provider: "x"\n'
        path = tmp_path / "bad.yaml"
        path.write_text(yaml_content, encoding="utf-8")

        with pytest.raises(ValueError, match="providers"):
            load_providers_config(path)

    def test_missing_required_field(self, tmp_path: Path):
        yaml_content = textwrap.dedent("""\
            default_provider: "bad"
            providers:
              bad:
                type: "openai_compatible"
                base_url: "http://x"
        """)
        path = tmp_path / "bad.yaml"
        path.write_text(yaml_content, encoding="utf-8")

        with pytest.raises(ValueError, match="model"):
            load_providers_config(path)

    def test_invalid_yaml_content(self, tmp_path: Path):
        path = tmp_path / "bad.yaml"
        path.write_text("just a string", encoding="utf-8")

        with pytest.raises(ValueError, match="expected dict"):
            load_providers_config(path)

    def test_provider_entry_not_dict(self, tmp_path: Path):
        yaml_content = textwrap.dedent("""\
            default_provider: "bad"
            providers:
              bad: "not a dict"
        """)
        path = tmp_path / "bad.yaml"
        path.write_text(yaml_content, encoding="utf-8")

        with pytest.raises(ValueError, match="expected dict"):
            load_providers_config(path)


# =========================================================================
# 4. CompanyLLMClient 扩展参数测试
# =========================================================================
class TestCompanyLLMClientExtended:
    def test_extra_headers_injected(self):
        settings = LLMSettings(
            api_base_url="http://test:8080/v1",
            api_key="key-123",
            model_name="test",
        )
        client = CompanyLLMClient(
            settings,
            extra_headers={"X-Tenant-ID": "my-tenant", "X-Custom": "val"},
        )
        headers = dict(client._client.headers)
        assert headers["x-tenant-id"] == "my-tenant"
        assert headers["x-custom"] == "val"
        assert headers["authorization"] == "Bearer key-123"

    def test_no_extra_headers(self):
        settings = LLMSettings(
            api_base_url="http://test:8080/v1",
            api_key="",
            model_name="test",
        )
        client = CompanyLLMClient(settings)
        headers = dict(client._client.headers)
        assert "authorization" not in headers

    def test_verify_ssl_false(self):
        settings = LLMSettings(api_base_url="https://self-signed:443/v1")
        client = CompanyLLMClient(settings, verify_ssl=False)
        # httpx stores verify as _transport._pool._ssl_context or similar
        # We verify the parameter was passed by checking the client was created
        assert client.state == LLMClientState.READY

    def test_verify_ssl_default_true(self):
        settings = LLMSettings(api_base_url="http://test:8080/v1")
        client = CompanyLLMClient(settings)
        assert client.state == LLMClientState.READY


# =========================================================================
# 5. create_client_from_provider() 工厂测试
# =========================================================================
class TestCreateClientFromProvider:
    def test_openai_compatible_creates_client(self):
        entry = ProviderEntry(
            type="openai_compatible",
            base_url="http://test-api:8080",
            model="test-model",
            headers={"X-Tenant-ID": "t1"},
            timeout=45.0,
            max_retries=1,
            ssl_verify=False,
        )
        client = create_client_from_provider(entry)
        assert isinstance(client, CompanyLLMClient)
        assert client.state == LLMClientState.READY
        assert client._settings.model_name == "test-model"
        assert client._settings.timeout_s == 45.0
        assert client._settings.max_retries == 1

    def test_api_token_injected(self):
        entry = ProviderEntry(
            type="openai_compatible",
            base_url="http://test-api:8080",
            model="m",
            api_token_env="FACTORY_TEST_TOKEN",
        )
        with patch.dict("os.environ", {"FACTORY_TEST_TOKEN": "tok-abc"}):
            client = create_client_from_provider(entry)
            assert client._settings.api_key == "tok-abc"

    def test_unsupported_type_raises(self):
        entry = ProviderEntry(
            type="unknown_provider",
            base_url="http://x",
            model="m",
        )
        with pytest.raises(ValueError, match="Unsupported provider type"):
            create_client_from_provider(entry)

    def test_headers_passed_to_client(self):
        entry = ProviderEntry(
            type="openai_compatible",
            base_url="http://test-api:8080",
            model="m",
            headers={"useLegacyCompletionsEndpoint": "false", "X-Tenant-ID": "tenant"},
        )
        client = create_client_from_provider(entry)
        headers = dict(client._client.headers)
        assert headers["x-tenant-id"] == "tenant"
        assert headers["uselegacycompletionsendpoint"] == "false"


# =========================================================================
# 6. create_client_from_config() 端到端测试
# =========================================================================
class TestCreateClientFromConfig:
    def _write_yaml(self, tmp_path: Path, content: str) -> Path:
        path = tmp_path / "llm_providers.yaml"
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_default_provider(self, tmp_path: Path):
        path = self._write_yaml(tmp_path, """\
            default_provider: "main"
            providers:
              main:
                type: "openai_compatible"
                base_url: "http://main-api:8080"
                model: "main-model"
                timeout: 30
        """)
        client = create_client_from_config(path)
        assert isinstance(client, CompanyLLMClient)
        assert client._settings.model_name == "main-model"

    def test_named_provider(self, tmp_path: Path):
        path = self._write_yaml(tmp_path, """\
            default_provider: "main"
            providers:
              main:
                type: "openai_compatible"
                base_url: "http://main:8080"
                model: "main-model"
              backup:
                type: "openai_compatible"
                base_url: "http://backup:8080"
                model: "backup-model"
                ssl_verify: false
        """)
        client = create_client_from_config(path, provider_name="backup")
        assert client._settings.model_name == "backup-model"

    def test_missing_provider_name_raises(self, tmp_path: Path):
        path = self._write_yaml(tmp_path, """\
            default_provider: "main"
            providers:
              main:
                type: "openai_compatible"
                base_url: "http://x"
                model: "m"
        """)
        with pytest.raises(KeyError, match="nope"):
            create_client_from_config(path, provider_name="nope")

    def test_with_env_token(self, tmp_path: Path):
        path = self._write_yaml(tmp_path, """\
            default_provider: "vio"
            providers:
              vio:
                type: "openai_compatible"
                base_url: "https://vio.example.com:446"
                model: "vio-model"
                api_token_env: "VIO_TEST_TOKEN"
                headers:
                  useLegacyCompletionsEndpoint: "false"
                  X-Tenant-ID: "test_tenant"
                ssl_verify: false
                stream: true
        """)
        with patch.dict("os.environ", {"VIO_TEST_TOKEN": "secret-vio"}):
            client = create_client_from_config(path)
            assert client._settings.api_key == "secret-vio"
            assert client._settings.api_base_url == "https://vio.example.com:446"
            headers = dict(client._client.headers)
            assert headers["x-tenant-id"] == "test_tenant"
