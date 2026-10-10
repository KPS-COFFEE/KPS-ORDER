document.addEventListener('click', async (event) => {
  if (event.target.closest('[data-print-report]')) {
    // Printing waits for the actual Arabic font, so the preview and paper match.
    await document.fonts.ready;
    window.print();
  }
});
document.querySelectorAll('#orders tbody tr').forEach((row) => {
  const reference = row.firstElementChild?.textContent;
  const detail = reference && document.getElementById('order-' + reference);
  if (!detail) return;
  const link = document.createElement('a');
  link.href = '#order-' + reference;
  link.textContent = reference;
  link.addEventListener('click', () => { detail.open = true; });
  row.firstElementChild.replaceChildren(link);
});
