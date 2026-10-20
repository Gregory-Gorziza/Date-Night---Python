async function readJsonResponse(response) {
    var contentType = response.headers.get('content-type') || '';
    var responseText = await response.text();
    if (!contentType.includes('application/json')) {
        console.error('Resposta inesperada do servidor:', {
            status: response.status,
            contentType: contentType,
            body: responseText.slice(0, 300)
        });
        throw new Error('O servidor não retornou JSON (HTTP ' + response.status + ').');
    }
    return JSON.parse(responseText);
}

document.addEventListener('DOMContentLoaded', function () {
    var page = document.querySelector('.matches-page');
    var dialog = document.getElementById('debugDialog');
    var openButton = document.getElementById('debugOpen');
    var closeButton = document.getElementById('debugClose');
    if (dialog && openButton && closeButton) {
        openButton.addEventListener('click', function () { dialog.showModal(); });
        closeButton.addEventListener('click', function () { dialog.close(); });
    }

    async function syncMatchStates() {
        if (!page) return;
        try {
            var response = await fetch(page.dataset.statusUrl, { headers: { 'Accept': 'application/json' } });
            if (!response.ok) return;
            var result = await readJsonResponse(response);
            result.matches.forEach(function (match) {
                var card = document.querySelector('[data-match-id="' + match.id + '"]');
                if (!card || card.dataset.state === match.state) return;
                card.dataset.state = match.state;
                var state = card.querySelector('.match-state');
                var messages = {
                    pending: 'Houve um Match! Confirme sua participação no encontro.',
                    waiting: 'Você confirmou! Aguardando a confirmação do seu par.',
                    confirmed: 'Encontro confirmado! Vocês dois aceitaram.',
                    declined: 'Este encontro foi recusado.'
                };
                state.textContent = messages[match.state];
                if (match.state === 'confirmed' && result.event_date) {
                    var eventTime = document.createElement('p');
                    eventTime.className = 'event-time';
                    eventTime.textContent = 'Data e hora: ' + result.event_date;
                    state.appendChild(eventTime);
                }
            });
            if (typeof result.confirmed_count === 'number') {
                var confirmedCount = document.getElementById('confirmedCount');
                var remainingCount = document.getElementById('remainingCount');
                if (confirmedCount && remainingCount) {
                    confirmedCount.textContent = result.confirmed_count;
                    remainingCount.textContent = Math.max(result.capacity - result.confirmed_count, 0) + ' vagas para atingir a capacidade';
                }
            }
        } catch (error) {
            return;
        }
    }

    window.setInterval(syncMatchStates, 15000);

    document.querySelectorAll('.confirmation-form').forEach(function (form) {
        form.addEventListener('submit', async function (event) {
            event.preventDefault();
            var button = event.submitter;
            var data = new FormData(form);
            data.set('action', button.value);
            button.disabled = true;
            try {
                var response = await fetch(form.action, {
                    method: 'POST',
                    body: data,
                    headers: { 'X-Requested-With': 'XMLHttpRequest' }
                });
                var result = await readJsonResponse(response);
                if (!response.ok) throw new Error(result.error || 'Não foi possível atualizar o encontro.');
                var container = form.closest('.match-row');
                container.dataset.state = result.state;
                var state = container.querySelector('.match-state');
                state.textContent = result.message;
                if (result.event_date) {
                    var eventTime = document.createElement('p');
                    eventTime.className = 'event-time';
                    eventTime.textContent = 'Data e hora: ' + result.event_date;
                    state.appendChild(eventTime);
                }
                if (typeof result.confirmed_count === 'number') {
                    document.getElementById('confirmedCount').textContent = result.confirmed_count;
                    document.getElementById('remainingCount').textContent = Math.max(result.capacity - result.confirmed_count, 0) + ' vagas para atingir a capacidade';
                }
                syncMatchStates();
            } catch (error) {
                window.alert(error.message || 'Erro de conexão. Tente novamente.');
                button.disabled = false;
            }
        });
    });
});