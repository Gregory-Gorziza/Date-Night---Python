document.querySelectorAll('[data-photo-upload]').forEach((input) => {
    const form = input.closest('form');
    const preview = form.querySelector('[data-photo-preview]');
    const message = form.querySelector('[data-photo-message]');
    const removals = form.querySelectorAll('[name="remover_fotos"]');
    const existingCount = Number(input.dataset.existingCount || 0);
    const allowedTypes = new Set(['image/jpeg', 'image/png', 'image/webp']);
    const maxFileSize = 5 * 1024 * 1024;

    const updatePreview = () => {
        preview.replaceChildren();
        const files = Array.from(input.files || []);
        const removedCount = Array.from(removals).filter((checkbox) => checkbox.checked).length;
        const remainingCount = existingCount - removedCount;
        let error = '';

        if (remainingCount + files.length > 5) {
            error = 'O perfil pode ter até cinco fotos.';
        } else if (files.some((file) => !allowedTypes.has(file.type))) {
            error = 'Use arquivos JPG, PNG ou WebP.';
        } else if (files.some((file) => file.size > maxFileSize)) {
            error = 'Cada arquivo pode ter até 5 MB.';
        }

        input.setCustomValidity(error);
        message.textContent = error || (files.length ? `${files.length} foto(s) selecionada(s).` : '');

        files.forEach((file) => {
            if (!allowedTypes.has(file.type)) {
                return;
            }
            const figure = document.createElement('figure');
            const image = document.createElement('img');
            image.src = URL.createObjectURL(file);
            image.alt = file.name;
            const caption = document.createElement('figcaption');
            caption.textContent = file.name;
            figure.append(image, caption);
            preview.append(figure);
        });
    };

    input.addEventListener('change', updatePreview);
    removals.forEach((checkbox) => checkbox.addEventListener('change', updatePreview));
    form.addEventListener('submit', (event) => {
        updatePreview();
        if (!input.checkValidity()) {
            event.preventDefault();
            input.reportValidity();
        }
    });
});
