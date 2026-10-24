document.addEventListener('click', async (event) => {
  if (event.target.closest('[data-print-report]')) {
    // Printing waits for the actual Arabic font, so the preview and paper match.
    await document.fonts.ready;
    window.print();
  }
});
document.querySelectorAll('#orders tbody tr').forEach((row) => {
  const reference = row.firstElementChild?.textContent;
  const target = row.dataset.drillTarget || (reference && 'order-' + reference);
  const detail = target && document.getElementById(target);
  if (!detail) return;
  const link = document.createElement('a');
  link.href = '#' + target;
  link.textContent = reference;
  link.addEventListener('click', () => { detail.open = true; });
  row.firstElementChild.replaceChildren(link);
});
