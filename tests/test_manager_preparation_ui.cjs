const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('public/ops.js', 'utf8');

for (const language of ['ar', 'en']) {
  const context = {
    tr: (ar, en) => language === 'ar' ? ar : en,
    esc: String,
    state: {user: {delivery_context: {bootstrap: true}}},
  };
  vm.createContext(context);
  for (const name of ['apiError', 'managerWorkPanel']) {
    const definition = source.split('\n').find(line => line.startsWith(`  function ${name}(`));
    assert.ok(definition, `${name} exists`);
    vm.runInContext(definition, context);
  }
  const morningError = context.apiError({detail: {code: 'MORNING_MANAGER_SHIFT_HOURS'}}, 409);
  const bootstrapPanel = context.managerWorkPanel(null);
  context.state.user.delivery_context = {};
  const dutyPanel = context.managerWorkPanel(null);
  for (const text of [morningError, bootstrapPanel, dutyPanel]) {
    for (const time of ['04:00', '03:45', '12:30', '12:15']) assert.ok(text.includes(time));
    assert.ok(!text.includes('15:45'), 'morning schedule must not display evening preparation');
  }
  const eveningError = context.apiError({detail: {code: 'MANAGER_SHIFT_HOURS'}}, 409);
  context.state.user.delivery_context = null;
  for (const text of [eveningError, context.managerWorkPanel(null)]) {
    assert.ok(text.includes('15:45') && text.includes('16:00'));
    assert.ok(!text.includes('12:15'), 'evening schedule remains unchanged');
  }
}
console.log('Manager preparation schedule and error translations passed (Arabic and English).');
