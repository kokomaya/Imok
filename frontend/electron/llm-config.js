/**
 * LLM 配置读取 — 从 llm_providers.yaml + .env 构建前端可用的 LLM 配置。
 *
 * 单一职责：读取并解析 LLM 配置文件。
 * 纯函数模块，不依赖 Electron API 或全局状态。
 */

const path = require('path');
const fs = require('fs');

/**
 * 读取 llm_providers.yaml + .env，返回前端可用的 LLM 配置。
 * @param {string} backendRoot - backend 根目录路径
 * @param {string} [providerName] - 指定 provider 名称；缺省时读取 llm_active.json 覆盖，
 *                                   再回退到 yaml 的 default_provider
 * @returns {{ ok: boolean, config?: Object, error?: string }}
 */
function loadLLMConfig(backendRoot, providerName) {
  try {
    // 1. 读取 .env 到环境变量（简单 key=value 解析）
    const envPath = path.join(backendRoot, '.env');
    const envVars = {};
    if (fs.existsSync(envPath)) {
      const lines = fs.readFileSync(envPath, 'utf-8').split('\n');
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) continue;
        const eqIdx = trimmed.indexOf('=');
        if (eqIdx > 0) {
          envVars[trimmed.slice(0, eqIdx).trim()] = trimmed.slice(eqIdx + 1).trim();
        }
      }
    }

    // 2. 读取 llm_providers.yaml
    const yamlPath = path.join(backendRoot, 'config', 'llm_providers.yaml');
    if (!fs.existsSync(yamlPath)) {
      return { ok: false, error: `Config not found: ${yamlPath}` };
    }

    // 简易 YAML 解析 — 使用 js-yaml 如果可用
    let yaml;
    try {
      yaml = require('js-yaml');
    } catch (_) {
      yaml = null;
    }

    const yamlContent = fs.readFileSync(yamlPath, 'utf-8');
    let parsed;
    if (yaml) {
      parsed = yaml.load(yamlContent);
    } else {
      parsed = parseSimpleYaml(yamlContent);
    }

    // 选择 provider：显式指定 > llm_active.json 覆盖 > yaml 默认
    const activeOverride = providerName || readActiveProvider(backendRoot);
    let chosenName = parsed.default_provider;
    if (activeOverride && parsed.providers?.[activeOverride]) {
      chosenName = activeOverride;
    }

    const provider = parsed.providers?.[chosenName];
    if (!provider) {
      return { ok: false, error: `Provider '${chosenName}' not found in config` };
    }

    // 3. 解析 API token
    const tokenEnvKey = provider.api_token_env || '';
    const apiKey = tokenEnvKey
      ? (envVars[tokenEnvKey] || process.env[tokenEnvKey] || '')
      : (envVars['API_TOKEN'] || '');

    return {
      ok: true,
      config: {
        provider: chosenName,
        baseUrl: provider.base_url,
        model: provider.model,
        apiKey,
        headers: provider.headers || {},
        timeout: (provider.timeout || 60) * 1000,
        sslVerify: provider.ssl_verify !== false,
        stream: provider.stream !== false,
      },
    };
  } catch (err) {
    console.error('[llm-config] Failed to load LLM config:', err.message);
    return { ok: false, error: err.message };
  }
}

/** 读取 llm_active.json 中的当前 provider 覆盖（不存在返回空）。 */
function readActiveProvider(backendRoot) {
  try {
    const p = path.join(backendRoot, 'config', 'llm_active.json');
    if (!fs.existsSync(p)) return '';
    const data = JSON.parse(fs.readFileSync(p, 'utf-8'));
    return typeof data.provider === 'string' ? data.provider : '';
  } catch (_) {
    return '';
  }
}

/**
 * 列出所有可用 provider 名称与当前生效 provider。
 * @param {string} backendRoot
 * @returns {{ ok: boolean, providers?: Object[], active?: string, defaultProvider?: string, error?: string }}
 */
function listLLMProviders(backendRoot) {
  try {
    const yamlPath = path.join(backendRoot, 'config', 'llm_providers.yaml');
    if (!fs.existsSync(yamlPath)) {
      return { ok: false, error: `Config not found: ${yamlPath}` };
    }
    let yaml;
    try {
      yaml = require('js-yaml');
    } catch (_) {
      yaml = null;
    }
    const yamlContent = fs.readFileSync(yamlPath, 'utf-8');
    const parsed = yaml ? yaml.load(yamlContent) : parseSimpleYaml(yamlContent);

    const defaultProvider = parsed.default_provider || '';
    const override = readActiveProvider(backendRoot);
    const providers = Object.entries(parsed.providers || {}).map(([name, p]) => ({
      name,
      model: p.model || '',
      baseUrl: p.base_url || '',
      isLocal: /localhost|127\.0\.0\.1|ollama/i.test(String(p.base_url || '')),
    }));
    const active = (override && providers.some((p) => p.name === override))
      ? override
      : defaultProvider;

    return { ok: true, providers, active, defaultProvider };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

/**
 * 简易 YAML 解析器 — 仅支持 llm_providers.yaml 的扁平结构。
 * @param {string} content
 * @returns {Object}
 */
function parseSimpleYaml(content) {
  const result = { providers: {} };
  let currentProvider = null;
  let inHeaders = false;

  for (const raw of content.split('\n')) {
    const line = raw.replace(/\r$/, '').replace(/#.*$/, '').trimEnd();
    if (!line.trim()) continue;

    const indent = raw.search(/\S|$/);

    if (indent === 0 && line.includes('default_provider:')) {
      result.default_provider = line.split(':').slice(1).join(':').trim().replace(/['"]/g, '');
      currentProvider = null;
      inHeaders = false;
    } else if (indent === 0 && line.trim() === 'providers:') {
      continue;
    } else if (indent === 2 && line.trim().endsWith(':') && !line.trim().includes(' ')) {
      currentProvider = line.trim().replace(/:$/, '');
      result.providers[currentProvider] = {};
      inHeaders = false;
    } else if (indent === 4 && currentProvider) {
      const kv = line.trim();
      if (kv === 'headers:') {
        inHeaders = true;
        result.providers[currentProvider].headers = {};
      } else if (inHeaders) {
        inHeaders = false;
        const ci = kv.indexOf(':');
        if (ci > 0) {
          const k = kv.slice(0, ci).trim();
          let v = kv.slice(ci + 1).trim().replace(/['"]/g, '');
          if (v === 'true') v = true;
          else if (v === 'false') v = false;
          else if (/^\d+(\.\d+)?$/.test(v)) v = Number(v);
          result.providers[currentProvider][k] = v;
        }
      } else {
        const ci = kv.indexOf(':');
        if (ci > 0) {
          const k = kv.slice(0, ci).trim();
          let v = kv.slice(ci + 1).trim().replace(/['"]/g, '');
          if (v === 'true') v = true;
          else if (v === 'false') v = false;
          else if (/^\d+(\.\d+)?$/.test(v)) v = Number(v);
          result.providers[currentProvider][k] = v;
        }
      }
    } else if (indent === 6 && currentProvider && inHeaders) {
      const kv = line.trim();
      const ci = kv.indexOf(':');
      if (ci > 0) {
        const k = kv.slice(0, ci).trim();
        const v = kv.slice(ci + 1).trim().replace(/['"]/g, '');
        result.providers[currentProvider].headers[k] = v;
      }
    }
  }

  return result;
}

module.exports = { loadLLMConfig, listLLMProviders };
