/**
 * Quick test: load LLM config the same way main.js does.
 * Run from project root: node scripts/test_llm_config.js
 */
const fs = require('fs');
const path = require('path');

const BACKEND_ROOT = path.resolve(__dirname, '..');

// ---- .env parsing (same as main.js) ----
const envPath = path.join(BACKEND_ROOT, '.env');
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
console.log('.env vars:', Object.keys(envVars));
console.log('API_TOKEN:', envVars['API_TOKEN'] ? envVars['API_TOKEN'].slice(0, 8) + '...' : 'MISSING');

// ---- YAML parsing (same as main.js) ----
const yamlPath = path.join(BACKEND_ROOT, 'config', 'llm_providers.yaml');
const yamlContent = fs.readFileSync(yamlPath, 'utf-8');

let yaml;
try {
  yaml = require('js-yaml');
  console.log('Using js-yaml');
} catch (_) {
  yaml = null;
  console.log('Using simple YAML parser');
}

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

const parsed = yaml ? yaml.load(yamlContent) : parseSimpleYaml(yamlContent);
console.log('\nParsed YAML:');
console.log(JSON.stringify(parsed, null, 2));

// ---- Config assembly (same as main.js) ----
const defaultName = parsed.default_provider;
const provider = parsed.providers?.[defaultName];
if (!provider) {
  console.error(`Provider '${defaultName}' not found!`);
  process.exit(1);
}

const tokenEnvKey = provider.api_token_env || '';
const apiKey = tokenEnvKey
  ? (envVars[tokenEnvKey] || process.env[tokenEnvKey] || '')
  : (envVars['API_TOKEN'] || '');

const config = {
  baseUrl: provider.base_url,
  model: provider.model,
  apiKey: apiKey ? apiKey.slice(0, 8) + '...' : 'EMPTY',
  headers: provider.headers || {},
  timeout: (provider.timeout || 60) * 1000,
  sslVerify: provider.ssl_verify !== false,
  stream: provider.stream !== false,
};

console.log('\nFinal config for renderer:');
console.log(JSON.stringify(config, null, 2));

// ---- Test fetch URL ----
const url = `${config.baseUrl.replace(/\/+$/, '')}/chat/completions`;
console.log('\nWould fetch:', url);
console.log('Stream mode:', config.stream);
