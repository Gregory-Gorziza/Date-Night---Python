const feed = document.getElementById('tiktokContainer');
const previousButton = document.getElementById('previousProfileBtn');
let activeSlide = feed?.querySelector('.tiktok-slide:not(.empty)') || null;
let backUsed = false;
let scrollTimer;
let viewQueue = Promise.resolve();

function feedSlides() {
    return [...(feed?.querySelectorAll('.tiktok-slide:not(.empty)') || [])];
}

function updatePreviousButton() {
    if (!previousButton) return;
    const index = feedSlides().indexOf(activeSlide);
    const available = index > 0 && !backUsed;
    previousButton.hidden = !available;
    previousButton.disabled = !available;
}

function scrollToSlide(slide) {
    if (!feed || !slide) return;
    const top = slide.getBoundingClientRect().top
        - feed.getBoundingClientRect().top
        + feed.scrollTop;
    feed.scrollTo({ top, behavior: 'smooth' });
}

function nearestSlide() {
    const slides = feedSlides();
    if (!feed || !slides.length) return null;
    const center = feed.getBoundingClientRect().top + feed.clientHeight / 2;
    return slides.reduce((nearest, slide) => {
        const distance = Math.abs(slide.getBoundingClientRect().top + slide.clientHeight / 2 - center);
        return !nearest || distance < nearest.distance ? { slide, distance } : nearest;
    }, null).slide;
}

function parseJsonResponse(response, responseText) {
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
        throw new Error(`Resposta não JSON (HTTP ${response.status}): ${responseText.slice(0, 160)}`);
    }
    return JSON.parse(responseText);
}

function recordProfileView(slide) {
    if (!slide || slide.dataset.viewRecorded === 'true' || slide.dataset.reacted === 'true') {
        return viewQueue;
    }
    slide.dataset.viewRecorded = 'true';
    const body = new FormData();
    body.set('expected_count', slide.dataset.appearanceIndex);

    const request = viewQueue.then(async () => {
        const response = await fetch(slide.dataset.viewUrl, {
            method: 'POST',
            body,
            headers: {
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
            },
        });
        const responseText = await response.text();
        const result = parseJsonResponse(response, responseText);
        console.log('Visualização registrada:', {
            perfil_id: slide.dataset.profileId,
            aparicao: result.contador_aparicoes,
            status: response.status,
        });
        if (!response.ok || !result.success) {
            throw new Error(result.error || 'Não foi possível registrar a visualização.');
        }
        return true;
    });

    viewQueue = request.catch((error) => {
        slide.dataset.viewRecorded = 'false';
        console.error('Falha ao registrar visualização:', error);
        return false;
    });
    return viewQueue;
}

function addEmptySlide() {
    if (!feed || feed.querySelector('.tiktok-slide.empty')) return;
    const emptySlide = document.createElement('div');
    emptySlide.className = 'tiktok-slide empty';
    emptySlide.textContent = 'Nenhum perfil disponível no momento';
    feed.appendChild(emptySlide);
}

function advanceFeed(slide, removeProfile) {
    const slides = feedSlides();
    const currentIndex = slides.indexOf(slide);
    const profileId = slide.dataset.profileId;
    const nextSlide = slides.slice(currentIndex + 1).find((item) => (
        !removeProfile || item.dataset.profileId !== profileId
    ));

    if (removeProfile) {
        slides.filter((item) => item.dataset.profileId === profileId)
            .forEach((item) => item.remove());
    } else {
        recordProfileView(slide);
    }

    if (nextSlide?.isConnected) {
        activeSlide = nextSlide;
        updatePreviousButton();
        scrollToSlide(nextSlide);
    } else {
        activeSlide = null;
        addEmptySlide();
        updatePreviousButton();
    }
}

function settleFeedScroll() {
    const nextSlide = nearestSlide();
    if (!nextSlide || nextSlide === activeSlide) return;

    const slides = feedSlides();
    const previousIndex = slides.indexOf(activeSlide);
    const nextIndex = slides.indexOf(nextSlide);
    if (nextIndex < previousIndex) {
        if (backUsed || nextIndex !== previousIndex - 1) {
            scrollToSlide(activeSlide);
            return;
        }
        backUsed = true;
    } else if (previousIndex >= 0) {
        for (let index = previousIndex; index < nextIndex; index += 1) {
            recordProfileView(slides[index]);
        }
    }

    activeSlide = nextSlide;
    updatePreviousButton();
}

feed?.addEventListener('scroll', () => {
    window.clearTimeout(scrollTimer);
    scrollTimer = window.setTimeout(settleFeedScroll, 140);
});

previousButton?.addEventListener('click', () => {
    const slides = feedSlides();
    const index = slides.indexOf(activeSlide);
    if (index > 0 && !backUsed) scrollToSlide(slides[index - 1]);
});

document.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowUp' && previousButton && !previousButton.hidden) {
        event.preventDefault();
        previousButton.click();
    }
});

document.addEventListener('click', async (event) => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest('.side-btn[type="submit"]');
    if (!button) return;

    const form = button.form;
    const slide = button.closest('.tiktok-slide');
    if (!form || !slide || !feed) return;
    const actionUrl = form.getAttribute('action');
    if (!actionUrl) return;

    event.preventDefault();
    const buttons = slide.querySelectorAll('.side-btn');
    buttons.forEach((reactionButton) => { reactionButton.disabled = true; });
    const payload = new FormData(form);
    payload.set('action', button.value);
    payload.set('perfil_id', slide.dataset.profileId);
    payload.set('tipo_reacao', button.value === 'like' ? 'gostei' : 'amei');
    console.log('Clique em reação:', {
        url: actionUrl,
        perfil_id: slide.dataset.profileId,
        tipo_reacao: payload.get('tipo_reacao'),
    });

    try {
        const response = await fetch(actionUrl, {
            method: 'POST',
            body: payload,
            headers: {
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
            },
        });
        const responseText = await response.text();
        const result = parseJsonResponse(response, responseText);
        console.log('Resposta da reação:', { url: response.url, status: response.status, ok: response.ok });
        if (!response.ok || !result.success) {
            throw new Error(result.error || 'Não foi possível salvar sua reação.');
        }

        const likeButton = slide.querySelector('.side-btn.btn-like');
        const loveButton = slide.querySelector('.side-btn.btn-love');
        likeButton.classList.toggle('is-selected', result.liked);
        likeButton.classList.toggle('is-muted', result.loved);
        loveButton.classList.toggle('is-selected', result.loved);
        loveButton.classList.toggle('is-muted', result.liked);
        likeButton.setAttribute('aria-pressed', String(result.liked));
        loveButton.setAttribute('aria-pressed', String(result.loved));

        const feedback = slide.querySelector('.reaction-feedback');
        feedback.textContent = result.matched ? 'É um Match!' : result.reaction ? `${button.textContent.trim()} salvo` : 'Reação removida';
        slide.dataset.reacted = String(Boolean(result.reaction));

        if (result.reaction) {
            advanceFeed(slide, true);
        } else {
            await recordProfileView(slide);
            advanceFeed(slide, false);
        }
    } catch (error) {
        console.error('Falha ao salvar reação:', error);
        slide.querySelector('.reaction-feedback').textContent = error.message || 'Erro de conexão. Tente novamente.';
    } finally {
        buttons.forEach((reactionButton) => { reactionButton.disabled = false; });
    }
});

updatePreviousButton();