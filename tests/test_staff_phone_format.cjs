const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('public/assets/customer-order.js', 'utf8');
const functions = source.match(/    function compactPhone\(value\).*\n    function normalizedPhone\(value\).*\n/);
assert.ok(functions, 'Test the actual staff form phone functions');
const phone = {value: ''};
let onInput;
phone.addEventListener = (event, fn) => { assert.equal(event, 'input'); onInput = fn; };
const sandbox = {form: {elements: {phone}}};
vm.createContext(sandbox);
const listener = source.split('\n').find(line => line.includes("addEventListener('input',()=>{const value=form.elements.phone.value;"));
assert.ok(listener);
vm.runInContext(functions[0] + listener, sandbox);
for (const pasted of ['+966 53 104 1449', '\u200e+966 53 104 1449\u200e',
  '\u2066+966\u00a053\u202f104 1449\u2069', '٠٥٣ ١٠٤ ١٤٤٩',
  '۰۵۳ ۱۰۴ ۱۴۴۹', '00966 (53) 104-1449']) {
  phone.value = pasted;
  onInput();
  assert.equal(phone.value, '+966531041449');
}
for (const invalid of ['Ahmed +966531041449', '++966531041449', '+9665310414499']) {
  phone.value = invalid;
  onInput();
  assert.equal(sandbox.normalizedPhone(phone.value), null);
}
console.log('Staff WhatsApp phone paste: PASS');
