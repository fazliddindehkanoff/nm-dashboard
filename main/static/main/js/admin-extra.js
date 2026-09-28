(function () {
  'use strict';

  // Show typed money amounts as "1 200 000 so‘m" so an extra zero is noticed.
  var MONEY_INPUTS = 'input[name="amount"], input[name$="-amount"], input[name="price"], input[name="minimum_booking_amount"]';

  function formatMoney(value) {
    var number = Number(String(value).replace(/\s/g, '').replace(',', '.'));
    if (!value || !isFinite(number)) return '';
    return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(number).replace(/ | /g, ' ') + ' so‘m';
  }

  function attach(input) {
    if (input.dataset.moneyPreview || input.type === 'hidden') return;
    input.dataset.moneyPreview = '1';
    var hint = document.createElement('div');
    hint.className = 'money-preview';
    hint.setAttribute('aria-live', 'polite');
    var anchor = input.parentElement && getComputedStyle(input.parentElement).display.indexOf('flex') !== -1 ? input.parentElement : input;
    anchor.insertAdjacentElement('afterend', hint);
    var update = function () { hint.textContent = formatMoney(input.value); };
    input.addEventListener('input', update);
    update();
  }

  function scan() {
    document.querySelectorAll(MONEY_INPUTS).forEach(attach);
  }

  document.addEventListener('DOMContentLoaded', scan);
  document.addEventListener('formset:added', scan);
})();
