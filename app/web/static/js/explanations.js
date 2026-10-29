(() => {
  const cookie = document.cookie.split('; ').find(value => value.startsWith('explanations='));
  let enabled = !cookie || cookie.split('=')[1] !== 'off';
  const apply = () => {
    document.documentElement.dataset.explanations = enabled ? 'on' : 'off';
    document.querySelectorAll('[data-explanations-toggle]').forEach(button => {
      button.textContent = `Explanations: ${enabled ? 'On' : 'Off'}`;
      button.setAttribute('aria-pressed', String(enabled));
    });
  };
  apply();
  function positionDefinition(event) {
    const term = event.target.closest?.('.glossary-term');
    if (!term) return;
    requestAnimationFrame(() => {
      const note = term.querySelector('.glossary-definition');
      if (!note || !note.getClientRects().length) return;
      const anchor = term.getBoundingClientRect(), rect = note.getBoundingClientRect();
      note.style.position = 'fixed';
      note.style.left = `${Math.max(12, Math.min(anchor.left, innerWidth - rect.width - 12))}px`;
      note.style.top = `${anchor.bottom + rect.height > innerHeight - 12 ? Math.max(12,anchor.top - rect.height - 5) : anchor.bottom + 5}px`;
    });
  }
  for (const type of ['pointerover', 'focusin', 'toggle']) document.addEventListener(type, positionDefinition, true);
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.querySelectorAll('[data-explanations-toggle]').forEach(button => button.addEventListener('click', () => {
      enabled = !enabled;
      document.cookie = `explanations=${enabled ? 'on' : 'off'}; Path=/; Max-Age=31536000; SameSite=Lax`;
      apply();
    }));
  });
})();
