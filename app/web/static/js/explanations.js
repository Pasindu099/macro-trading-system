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
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.querySelectorAll('[data-explanations-toggle]').forEach(button => button.addEventListener('click', () => {
      enabled = !enabled;
      document.cookie = `explanations=${enabled ? 'on' : 'off'}; Path=/; Max-Age=31536000; SameSite=Lax`;
      apply();
    }));
  });
})();
