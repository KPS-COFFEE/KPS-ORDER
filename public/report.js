document.addEventListener('click', (event) => {
  if (event.target.closest('[data-print-report]')) window.print();
});
