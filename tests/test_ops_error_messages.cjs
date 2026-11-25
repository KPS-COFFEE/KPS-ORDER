const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('public/ops.js', 'utf8');
const definition = source.split('\n').find(line => line.startsWith('  function apiError('));
for (const language of ['ar', 'en']) {
  const context = {tr: (ar, en) => language === 'ar' ? ar : en};
  vm.createContext(context);
  vm.runInContext(definition, context);
  const message = 'حدد سبب ومسؤولية الطلبات الملغية والمرتجعة قبل الاعتماد';
  assert.equal(context.apiError({detail: message}, 409), message);
  assert.equal(context.apiError({detail: {code: 'CUSTOM', message}}, 409), message);
  assert.equal(typeof context.apiError({detail: [{loc: ['body'], msg: 'invalid'}]}, 422), 'string');
  assert.notEqual(context.apiError({}, 409), '');
}
console.log('API errors preserve concrete server messages in both languages.');
