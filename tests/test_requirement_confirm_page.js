const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync('frontend/requirement-confirm-page.js', 'utf8')
  .replace(/cfStart\(\);/, '')
  + '\nglobalThis.__setPrecheck = value => { cfPrecheck = value; };\n'
  + 'globalThis.__cfPrecheckRows = cfPrecheckRows;\n'
  + 'globalThis.__setRequirement = value => { cfRequirement = value; };\n'
  + 'globalThis.__cfRecommendationSection = cfRecommendationSection;\n'
  + 'globalThis.__cfAiNote = cfAiNote;';
const sandbox = {
  URLSearchParams,
  location: { search: '?project=test' },
  localStorage: { getItem: () => '' },
  document: {},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

assert.strictEqual(typeof sandbox.__cfPrecheckRows, 'function');
sandbox.__setPrecheck({ items: [
  { item: '一、需求基本信息（Section A）', status: 'ok', detail: '基础信息完整' },
  { item: '3.1 基础参数', status: 'need_info', detail: '<待补充>' },
] });
const rows = sandbox.__cfPrecheckRows();
assert(rows.includes('基础信息完整'));
assert(rows.includes('&lt;待补充&gt;'));
assert(rows.includes('class="need-info"'));

sandbox.__setPrecheck({ items: [] });
assert(sandbox.__cfPrecheckRows().includes('暂无检查结果'));

sandbox.__setRequirement({ data: {
  document_extraction: { recommendations: { product_type: 'esc' }, all_recommended_fields: ['product_type'] },
} });
const recommendation = sandbox.__cfRecommendationSection();
assert(recommendation.includes('is-collapsed'));
assert(recommendation.includes('aria-expanded="false"'));
assert(recommendation.includes('ai-recommendation-body'));

sandbox.__setPrecheck({ engine: 'qwen', model: 'direct-deepseek-v4-flash', model_source: 'runtime_actual', checked_at: '刚刚' });
assert(sandbox.__cfAiNote().includes('模型：direct-deepseek-v4-flash'));
assert(!sandbox.__cfAiNote().includes('Qwen AI 检查'));
sandbox.__setPrecheck({ engine: 'qwen', model: '', checked_at: '刚刚' });
assert(sandbox.__cfAiNote().includes('实际模型未留痕'));

console.log('requirement-confirm page renderer regression: passed');
