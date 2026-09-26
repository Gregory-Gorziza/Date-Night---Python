document.addEventListener('keydown', (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.target.matches('input, select, textarea')) {
        return;
    }

    if (event.key === 'ArrowUp') {
        const previous = document.querySelector('[aria-label="Perfil anterior"]');
        if (previous) {
            event.preventDefault();
            window.location.assign(previous.href);
        }
        return;
    }

    const actionByKey = {
        ArrowLeft: 'love',
        ArrowRight: 'like',
        ArrowDown: 'skip',
    };
    const action = actionByKey[event.key];
    if (!action) {
        return;
    }

    const button = document.querySelector(`[data-match-actions] button[value="${action}"]`);
    if (button) {
        event.preventDefault();
        button.click();
    }
});
