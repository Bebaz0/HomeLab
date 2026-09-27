# insta-bot

Mantém o thread do grupo aberto no Chrome real (headful, em Xvfb :99) entre as ~16:45 e as ~00:05. Responde "bora" e afins a convites para jogar, com um cooldown global de 1 h:
- `lol` sozinho só conta em mensagens até 5 palavras, e não conta se a mensagem for só riso ("lol", "kkkk lol");
- termos de LoL (`flex`, `league`, `ranked(s)`, `soloq`, `duo q`, `aram(s)`, `clash`) e convites como "siga jogar", "vamos jogar?", "quem joga?", "alguém umas rankeds?" contam até 12 palavras.

Os gatilhos e as respostas estão em `trigger.py`.

## Instalação (uma vez)

```bash
# como root
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
apt install ./google-chrome-stable_current_amd64.deb xvfb x11vnc x11-utils python3-venv
locale-gen pt_PT.UTF-8

# como bebaz
cd ~/homelab/insta-bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/pytest -q tests
cp .env.example .env && chmod 600 .env     # preencher depois da Fase 0
mkdir -p ~/.config/systemd/user
ln -sf $PWD/systemd/insta-bot* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now insta-bot-web.service insta-bot.timer
```

## Fase 0: login (e recuperação)

```bash
./phase0.sh                                 # no servidor
ssh -L 5900:localhost:5900 bebaz@servidor   # no PC, e depois VNC para localhost:5900
```

1. Faz login e resolve a verificação, se aparecer.
2. Dispensa os popups.
3. Abre o grupo e copia o URL para `THREAD_URL` no `.env`.
4. Fecha o Chrome pela janela, sem fazer logout.

## Afinar seletores (antes de ligar o timer)

O `observer.js` usa `[role="main"]` e `div[role="group"]:not([aria-label])` por omissão, e lê o remetente do aria-label "Reagir à mensagem de <user>". Se o arranque falhar com *conversation container not found*:
1. Abre o thread pela Fase 0 e usa o DevTools (F12) para ver que role ou aria-label tem a lista de mensagens.
2. Mete os valores em `CONTAINER_SELECTOR` / `ROW_SELECTOR` no `.env`.

Para testar sem esperar pelo timer:

```bash
systemctl --user start insta-bot && journalctl --user -fu insta-bot   # dentro do horário do painel
```

Escreve "bora lol" do telemóvel de outra conta. Deve aparecer `-> reply` e `dry_run`, e a seguinte deve dar `cooldown`. Uma mensagem tua deve dar `ignored:self`.

## Painel (http://insta-bot.homelab)

O `insta-bot-web.service` (sempre ligado) serve o painel em `172.17.0.1:8091`. O Caddy expõe-o como `insta-bot.homelab`, com password. No painel podes:
- ligar e desligar o bot (desligar fecha o Chrome logo);
- mudar o horário e o cooldown;
- alternar o modo treino (dry-run);
- editar as respostas e os gatilhos;
- fazer reset do cooldown e limpar um erro de paragem;
- ver as respostas recentes e o histórico.

Tudo fica em `data/config.json`, e o bot relê o ficheiro a cada mensagem e a cada 10 s, por isso não é preciso reiniciar nada.

O `insta-bot.timer` corre a cada ~5 min. O `bot.py --should-run` só deixa arrancar se o bot estiver ligado no painel, dentro do horário e sem `data/STOPPED`.

Sintaxe dos gatilhos:
- `*` = quaisquer palavras pelo meio;
- `/` = alternativas;
- maiúsculas e acentos são ignorados.

Por exemplo, `siga/bora * jogar` apanha "Siga lá jogar?".

## Operação

| | |
|---|---|
| Logs | `journalctl --user -u insta-bot` ou `data/bot.log` |
| Histórico (SQLite) | `data/bot.db` |
| Pausar respostas sem fechar o Chrome | `touch data/PAUSE` (apagar para retomar) |
| Painel em baixo? | `systemctl --user status insta-bot-web` |

## Quando pára

Login, verificação, "atividade suspeita", 3 reloads falhados ou qualquer erro inesperado fazem o seguinte:
- tira um screenshot para `data/fail-*.png`;
- envia uma notificação ntfy, se o `NTFY_URL` estiver definido (é opcional);
- cria o ficheiro `data/STOPPED`.

Sem ntfy, verifica de vez em quando:

```bash
cat ~/homelab/insta-bot/data/STOPPED 2>/dev/null || echo OK
```

Enquanto `data/STOPPED` existir, o timer não volta a arrancar o bot. Resolve pela Fase 0: o `phase0.sh` apaga o `STOPPED` no fim.

Reloads: até 3 de recuperação por sessão, com pelo menos 10 min entre eles. Há também um reload preventivo se a página estiver 1 h sem atividade. Um reload nunca faz login, e se aparecer o ecrã de login o bot pára.
