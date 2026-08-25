# nginx na Prática

Guia de referência de nginx: do primeiro server block ao Ingress Controller no Kubernetes. Fundamentos, reverse proxy, TLS, segurança, módulos e tuning de performance, com configurações prontas para adaptar.

## Sumário

1. [Fundamentos](#1-fundamentos)
2. [Conteúdo estático](#2-conteúdo-estático)
3. [Reverse proxy e load balancing](#3-reverse-proxy-e-load-balancing)
4. [TLS, HTTPS e segurança](#4-tls-https-e-segurança)
5. [Ingress no Kubernetes](#5-ingress-no-kubernetes)
6. [Performance e tuning](#6-performance-e-tuning)
7. [Módulos e ecossistema](#7-módulos-e-ecossistema)
8. [Troubleshooting](#8-troubleshooting)
9. [Referências](#9-referências)

---

## 1. Fundamentos

O nginx é um servidor web, reverse proxy e load balancer. A razão de ele estar em todo lugar (inclusive por trás do Ingress Controller mais usado no Kubernetes) é a arquitetura: em vez de criar uma thread ou processo por conexão, ele usa um modelo **event-driven e assíncrono**, em que poucos processos atendem dezenas de milhares de conexões simultâneas.

### Arquitetura: master e workers

- **Master process**: roda como root, lê a configuração, abre as portas (80/443) e gerencia os workers. Não atende requisições.
- **Worker processes**: atendem as conexões de fato. Cada worker é single-threaded e usa um event loop (epoll no Linux). Regra prática: um worker por núcleo de CPU.

Essa separação é o que permite o `reload` sem downtime: o master carrega a nova configuração, sobe workers novos e deixa os antigos terminarem as conexões em andamento antes de morrer.

### Instalação e ciclo de vida

```bash
# Debian/Ubuntu
sudo apt install nginx

# Testar a sintaxe da configuração (faça SEMPRE antes de recarregar)
sudo nginx -t

# Recarregar sem derrubar conexões
sudo systemctl reload nginx     # ou: nginx -s reload

# Ver a configuração final, com todos os includes resolvidos
sudo nginx -T
```

### Estrutura da configuração

Tudo em nginx é **diretiva** (linha terminada em `;`) organizada em **contextos** (blocos entre chaves). Os contextos se aninham e a maioria das diretivas herda do contexto pai:

```nginx
user www-data;
worker_processes auto;              # contexto main

events {
    worker_connections 1024;        # conexões por worker
}

http {                              # tudo de HTTP vive aqui
    include mime.types;
    sendfile on;

    server {                        # um virtual host
        listen 80;
        server_name exemplo.com.br;

        location / {                # regras por caminho da URL
            root /var/www/site;
        }
    }
}
```

Na prática, distribuições separam os sites em arquivos: `/etc/nginx/conf.d/*.conf` ou `sites-available/` + symlink em `sites-enabled/`. O `nginx.conf` principal só faz `include` deles dentro do bloco `http`.

### Como o nginx escolhe o server block

Chegou uma requisição: o nginx primeiro casa o par IP:porta do `listen`, depois compara o header `Host` com os `server_name`. Se nada casar, ele usa o **default server** daquela porta (o primeiro definido, ou o marcado com `listen 80 default_server;`).

### Como o nginx escolhe o location

Essa é a parte que mais gera confusão. A ordem de precedência não é a ordem do arquivo:

| Modificador | Tipo | Prioridade |
|---|---|---|
| `location = /login` | Match exato | 1ª: se casar, para na hora |
| `location ^~ /static/` | Prefixo com trava | 2ª: melhor prefixo vence e regex não é consultada |
| `location ~ \.php$` | Regex (case-sensitive) | 3ª: primeira regex que casar, na ordem do arquivo |
| `location ~* \.(jpg\|png)$` | Regex (case-insensitive) | 3ª: mesma fila das regex |
| `location /api/` | Prefixo simples | 4ª: usado só se nenhuma regex casar |

> **Resumo mental:** exato > prefixo com `^~` > regex (na ordem) > maior prefixo simples. Quando um comportamento parecer "impossível", quase sempre é uma regex roubando a requisição de um prefixo.

### Variáveis

Diretivas aceitam variáveis embutidas: `$host`, `$uri`, `$args`, `$remote_addr`, `$request_uri` (URI original com query string), `$scheme`, `$server_name`. Elas aparecem o tempo todo em proxy, logs e redirects.

---

## 2. Conteúdo estático

### root vs alias

Os dois mapeiam URL para disco, mas de formas diferentes, e confundi-los é um clássico:

```nginx
location /imagens/ {
    root /var/www;          # /imagens/a.png -> /var/www/imagens/a.png (concatena a URI inteira)
}

location /imagens/ {
    alias /dados/fotos/;    # /imagens/a.png -> /dados/fotos/a.png (substitui o prefixo)
}
```

> **Atenção:** com `alias` em location de prefixo, termine os dois com `/`. Prefira `root` sempre que a estrutura de pastas permitir; `alias` tem mais casos de borda.

### try_files: a espinha dorsal de SPAs

`try_files` testa caminhos em ordem e usa o último argumento como fallback. É o padrão para aplicações React/Vue/Angular com roteamento no browser:

```nginx
server {
    listen 80;
    server_name app.exemplo.com.br;
    root /var/www/app/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;   # arquivo? diretório? senão, index.html
    }

    location ~* \.(js|css|png|svg|woff2)$ {
        expires 30d;                        # cache no browser p/ assets com hash no nome
        add_header Cache-Control "public, immutable";
    }
}
```

### Páginas de erro e logs

```nginx
error_page 404 /404.html;
error_page 500 502 503 504 /50x.html;

access_log /var/log/nginx/app.access.log;
error_log  /var/log/nginx/app.error.log warn;   # debug|info|notice|warn|error|crit
```

---

## 3. Reverse proxy e load balancing

Reverse proxy é o papel mais comum do nginx em produção: ele fica na frente da aplicação (Node, Python, Java, um container qualquer), termina o TLS e repassa as requisições. O cliente só enxerga o nginx.

### proxy_pass essencial

```nginx
server {
    listen 80;
    server_name api.exemplo.com.br;

    location / {
        proxy_pass http://127.0.0.1:3000;

        # Sem estes headers, a aplicação enxerga tudo vindo do nginx
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### A barra final do proxy_pass muda tudo

Detalhe pequeno, consequência enorme:

```nginx
location /api/ {
    proxy_pass http://backend:8080;     # SEM barra: backend recebe /api/users
}

location /api/ {
    proxy_pass http://backend:8080/;    # COM barra: /api/ é removido, backend recebe /users
}
```

> Sem URI no `proxy_pass`, a URI original passa intacta. Com URI (mesmo que seja só `/`), o nginx substitui o prefixo do location pelo caminho informado. Em location com regex, `proxy_pass` com URI nem é permitido.

### upstream e load balancing

```nginx
upstream app_backend {
    least_conn;                          # padrão é round-robin; há também ip_hash
    server 10.0.1.10:8080 weight=3;
    server 10.0.1.11:8080;
    server 10.0.1.12:8080 max_fails=3 fail_timeout=30s;
    server 10.0.1.13:8080 backup;        # só entra se os demais caírem

    keepalive 32;                        # pool de conexões com o backend
}

server {
    location / {
        proxy_pass http://app_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";   # necessário p/ keepalive funcionar
        proxy_next_upstream error timeout http_502 http_503;
    }
}
```

- **round-robin** (padrão): distribui na sequência, respeitando `weight`.
- **least_conn**: manda para quem tem menos conexões ativas. Bom para requisições de duração variada.
- **ip_hash**: mesmo cliente sempre no mesmo backend (sticky por IP). Usado quando a aplicação guarda sessão em memória.

O health check do nginx open source é **passivo**: `max_fails`/`fail_timeout` marcam o servidor como fora depois de erros reais. Health check ativo é feature do NGINX Plus (ou resolvido fora, como no Kubernetes, onde readiness probes tiram o pod do Service).

### WebSockets

```nginx
location /ws/ {
    proxy_pass http://app_backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade    $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;            # senão a conexão cai em 60s de silêncio
}
```

### Timeouts e buffering

| Diretiva | Padrão | O que controla |
|---|---|---|
| `proxy_connect_timeout` | 60s | Tempo para abrir conexão com o backend (na prática, ajuste para 5s ou menos) |
| `proxy_read_timeout` | 60s | Silêncio máximo esperando resposta. É o timeout do erro 504 |
| `proxy_send_timeout` | 60s | Silêncio máximo enviando a requisição ao backend |
| `proxy_buffering` | on | nginx absorve a resposta e libera o backend rápido. Desligue (`off`) para SSE/streaming |
| `client_max_body_size` | 1m | Tamanho máximo de upload. Estourou? Erro 413 |

---

## 4. TLS, HTTPS e segurança

### Configuração TLS moderna

```nginx
# Redireciona todo HTTP para HTTPS
server {
    listen 80;
    server_name exemplo.com.br;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;
    server_name exemplo.com.br;

    ssl_certificate     /etc/letsencrypt/live/exemplo.com.br/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/exemplo.com.br/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;       # nunca habilite TLS 1.0/1.1
    ssl_prefer_server_ciphers off;       # em TLS moderno, o cliente escolhe
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
}
```

> Para gerar a configuração de ciphers ideal para seu caso, use o **Mozilla SSL Configuration Generator** (perfil "intermediate" atende quase todo mundo). Certificados gratuitos: `certbot --nginx` emite pelo Let's Encrypt e já edita a configuração, com renovação automática via timer do systemd.

### Headers de segurança

```nginx
# HSTS: browser passa a exigir HTTPS por 2 anos. Só ative com o HTTPS estável!
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;

add_header X-Content-Type-Options nosniff always;
add_header X-Frame-Options DENY always;              # bloqueia clickjacking via iframe
add_header Referrer-Policy strict-origin-when-cross-origin always;

# Esconde a versão do nginx nos headers e páginas de erro
server_tokens off;
```

> **Pegadinha de herança:** se um `location` declarar qualquer `add_header` próprio, ele deixa de herdar TODOS os do nível acima. Centralize os headers num arquivo e faça `include` onde precisar.

### Rate limiting

O nginx limita requisições com o algoritmo leaky bucket. Definição no contexto `http`, aplicação por `location`:

```nginx
# Zona de 10 MB (~160 mil IPs), limite de 10 req/s por IP
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;
limit_conn_zone $binary_remote_addr zone=conn_limit:10m;

server {
    location /api/ {
        limit_req zone=api_limit burst=20 nodelay;  # tolera rajadas de 20
        limit_conn conn_limit 20;                   # máx. 20 conexões simultâneas por IP
        limit_req_status 429;                       # padrão seria 503
        proxy_pass http://app_backend;
    }
}
```

- **burst**: fila de tolerância para picos acima do rate.
- **nodelay**: atende a rajada imediatamente em vez de espaçar as respostas. Sem ele, requisições do burst são atrasadas para manter o ritmo.

### Restrições de acesso

```nginx
location /admin/ {
    allow 10.0.0.0/8;        # rede interna
    deny  all;

    auth_basic "Restrito";   # básico, sempre junto com HTTPS
    auth_basic_user_file /etc/nginx/.htpasswd;
}

# Bloquear acesso a dotfiles (.git, .env...)
location ~ /\. { deny all; }
```

---

## 5. Ingress no Kubernetes

O **ingress-nginx** (projeto da comunidade Kubernetes, diferente do "NGINX Ingress Controller" da F5) é um pod rodando nginx + um controller que observa os recursos `Ingress` do cluster. Cada vez que um Ingress, Service ou Endpoint muda, o controller **regenera o nginx.conf e faz reload**. Ou seja: tudo que você aprendeu acima continua valendo, só muda quem escreve a configuração.

### O caminho de uma requisição

Cliente -> LoadBalancer (Service do controller) -> pod do ingress-nginx -> **direto no IP do pod da aplicação** (o controller lê os Endpoints e faz o balanceamento ele mesmo, sem passar pelo kube-proxy).

### Um Ingress típico

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: app
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "120"
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  ingressClassName: nginx
  tls:
    - hosts: [app.exemplo.com.br]
      secretName: app-tls          # cert-manager preenche este Secret
  rules:
    - host: app.exemplo.com.br
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service: { name: app, port: { number: 80 } }
```

### Annotations que mapeiam para o que você já conhece

Prefixo: `nginx.ingress.kubernetes.io/`

| Annotation | Equivalente nginx |
|---|---|
| `proxy-body-size` | `client_max_body_size` (o 413 clássico em upload) |
| `proxy-read-timeout`, `proxy-send-timeout` | `proxy_read_timeout`, `proxy_send_timeout` |
| `rewrite-target` | `rewrite` (com capture groups no path) |
| `ssl-redirect` | `return 301 https://...` |
| `limit-rps`, `limit-connections` | `limit_req` / `limit_conn` |
| `affinity: cookie` | Sessão sticky (via cookie, melhor que ip_hash) |
| `backend-protocol` | `proxy_pass https://...` ou GRPC |
| `configuration-snippet` / `server-snippet` | nginx.conf cru injetado (frequentemente desabilitado por segurança) |

Configuração global do controller (para todos os Ingresses) fica no **ConfigMap** do ingress-nginx: ciphers, log format, gzip, keepalive, real IP quando atrás de outro proxy, etc.

### Debug do controller

```bash
# Ver o nginx.conf que o controller gerou
kubectl exec -n ingress-nginx deploy/ingress-nginx-controller -- cat /etc/nginx/nginx.conf

# Logs de acesso e de reload
kubectl logs -n ingress-nginx deploy/ingress-nginx-controller -f

# Eventos de um Ingress com problema (classe errada, secret ausente...)
kubectl describe ingress app
```

> Um 502 no ingress-nginx quase sempre significa: Service sem endpoints (label selector errado ou pods sem passar no readiness probe), porta errada no backend, ou pod recusando conexão. `kubectl get endpoints app` é o primeiro comando a rodar.

---

## 6. Performance e tuning

### Workers e conexões

```nginx
worker_processes auto;              # um por núcleo
worker_rlimit_nofile 65535;         # limite de file descriptors do worker

events {
    worker_connections 4096;        # por worker; inclui conexões com o backend
    multi_accept on;
}
```

Capacidade teórica = workers x worker_connections, lembrando que em modo proxy cada requisição consome duas conexões (cliente e backend).

### Entrega de arquivos e keepalive

```nginx
sendfile on;              # kernel copia arquivo direto p/ o socket
tcp_nopush on;            # envia headers + início do arquivo num pacote só
keepalive_timeout 65;
keepalive_requests 1000;

# Cacheia metadados de arquivos abertos (ótimo p/ muito estático)
open_file_cache max=10000 inactive=20s;
open_file_cache_valid 30s;
```

### Compressão

```nginx
gzip on;
gzip_comp_level 5;               # acima de 6 o ganho não paga a CPU
gzip_min_length 1024;            # não comprimir resposta minúscula
gzip_proxied any;
gzip_vary on;
gzip_types text/css application/javascript application/json image/svg+xml;
# Nunca inclua imagens/vídeo: já são comprimidos. Brotli existe via módulo ngx_brotli.
```

### Cache de proxy

O nginx pode guardar respostas do backend e servir de disco, aliviando a aplicação:

```nginx
# contexto http
proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=app_cache:10m
                 max_size=1g inactive=60m use_temp_path=off;

server {
    location /api/catalogo/ {
        proxy_cache app_cache;
        proxy_cache_valid 200 10m;
        proxy_cache_valid 404 1m;
        proxy_cache_use_stale error timeout updating http_502 http_503;
        proxy_cache_lock on;           # 1 requisição repovoa, as demais esperam
        add_header X-Cache-Status $upstream_cache_status;   # HIT/MISS/STALE p/ debug
        proxy_pass http://app_backend;
    }
}
```

> `proxy_cache_use_stale` é um seguro barato: se o backend cair, o nginx serve a última versão boa em vez de 502. Combinado com `proxy_cache_lock`, também evita o efeito manada quando um item popular expira.

### Checklist de tuning

- [ ] `worker_processes auto` + `worker_connections` >= 4096 e ulimit compatível
- [ ] Keepalive nos dois lados: com o cliente e no `upstream` (`keepalive N` + `proxy_http_version 1.1`)
- [ ] HTTP/2 ligado no listener TLS
- [ ] gzip para texto, `expires` longo para assets com hash no nome
- [ ] Buffering ligado por padrão; desligado apenas para streaming/SSE
- [ ] Logs: em picos extremos, `access_log off` em locations de assets ou buffer no log
- [ ] Métricas: `stub_status` habilitado (é o que o nginx-prometheus-exporter e o kube-prometheus-stack raspam)

---

## 7. Módulos e ecossistema

O nginx não tem "plugins" que você instala em runtime como num WordPress: a extensibilidade vem de **módulos**. Historicamente todo módulo era compilado junto com o binário; desde a versão 1.9.11 existem **módulos dinâmicos** (arquivos `.so`) carregados via configuração:

```nginx
# Topo do nginx.conf (contexto main)
load_module modules/ngx_http_brotli_filter_module.so;
load_module modules/ngx_http_geoip2_module.so;
```

```bash
# Ver com quais módulos seu binário foi compilado
nginx -V 2>&1 | tr ' ' '\n' | grep module

# Debian/Ubuntu empacotam vários prontos
apt search libnginx-mod-
```

> Nem todo módulo third-party existe como pacote pronto. Quando não existe, o caminho é compilar o módulo contra a mesma versão do seu nginx (`./configure --add-dynamic-module=...`), o que é chato de manter. Por isso, em containers, é comum usar imagens que já vêm com o módulo desejado, ou partir para o OpenResty.

### Módulos que valem conhecer

| Módulo | Para quê |
|---|---|
| `ngx_brotli` | Compressão Brotli (melhor que gzip para texto; o Google mantém) |
| `headers-more` | Alterar/remover qualquer header, inclusive o `Server:` inteiro (o `server_tokens off` só esconde a versão) |
| `geoip2` | Geolocalização por IP com as bases MaxMind: bloquear países, rotear por região, enriquecer logs |
| `nginx-module-vts` | Métricas detalhadas por vhost/upstream (o `stub_status` nativo é bem básico) |
| `ModSecurity` / `Coraza` | WAF: aplica regras OWASP Core Rule Set na frente da aplicação (SQLi, XSS, etc.) |
| `ngx_cache_purge` | Invalidar itens do proxy_cache sob demanda (nativo só no NGINX Plus) |
| `rtmp` | Streaming de vídeo ao vivo (RTMP/HLS), popular para restreaming |

### Scripting: njs e Lua/OpenResty

Quando configuração declarativa não basta (lógica de autenticação, manipulação de request/response, roteamento dinâmico), há dois caminhos:

- **njs**: o runtime JavaScript oficial do nginx. Leve, mantido pela F5, bom para transformações e auth simples. Diretivas como `js_content` e `js_set` chamam funções de um arquivo `.js`.
- **OpenResty**: distribuição do nginx com LuaJIT e um ecossistema enorme de bibliotecas. É a base de API gateways como o **Kong** e do **APISIX**. Se a sua necessidade parece "programar dentro do proxy", provavelmente a resposta madura é OpenResty ou um gateway construído sobre ele.

```nginx
# nginx.conf
load_module modules/ngx_http_js_module.so;
http {
    js_import main from /etc/nginx/njs/main.js;
    server {
        location /hello {
            js_content main.hello;
        }
    }
}
```

```javascript
// /etc/nginx/njs/main.js
function hello(r) {
    r.return(200, JSON.stringify({ ola: r.args.nome || "mundo" }));
}
export default { hello };
```

### Observabilidade

```nginx
server {
    listen 127.0.0.1:8080;
    location /stub_status {
        stub_status;
        allow 127.0.0.1;
        deny all;
    }
}
```

- **nginx-prometheus-exporter** raspa o `stub_status` e expõe métricas para o Prometheus; com o módulo VTS os dados ficam bem mais ricos.
- No Kubernetes, o ingress-nginx já expõe métricas Prometheus nativamente (latência, códigos de status por Ingress); há dashboards prontos no Grafana e integração direta com o kube-prometheus-stack.
- Logs estruturados: defina um `log_format` em JSON e o ingestor (Loki, ELK, CloudWatch) agradece.

```nginx
log_format json_log escape=json
  '{"time":"$time_iso8601","remote_addr":"$remote_addr",'
  '"request":"$request","status":$status,"bytes":$body_bytes_sent,'
  '"request_time":$request_time,"upstream_time":"$upstream_response_time",'
  '"host":"$host","user_agent":"$http_user_agent"}';

access_log /var/log/nginx/access.json json_log;
```

### O mapa do ecossistema

- **nginx open source vs NGINX Plus**: o Plus (comercial, da F5) adiciona health check ativo, API de reconfiguração dinâmica, sticky session avançada e dashboard.
- **OpenResty**: nginx + LuaJIT, base de gateways.
- **Kong / APISIX**: API gateways completos (auth, rate limit por consumer, plugins de verdade) construídos sobre nginx/OpenResty.
- **ingress-nginx**: o controller de Kubernetes da seção 5.
- **Concorrentes que valem conhecer para comparar**: Caddy (TLS automático por padrão), Traefik (descoberta dinâmica de serviços, comum em Docker/K8s), HAProxy (balanceador L4/L7 de altíssima performance) e Envoy (proxy dos service meshes, como Istio).

---

## 8. Troubleshooting

### Erros clássicos e onde olhar

| Sintoma | Causa mais comum |
|---|---|
| 502 Bad Gateway | Backend fora do ar, porta errada no `proxy_pass`, ou (no K8s) Service sem endpoints. Confirme no error_log: "connection refused" |
| 504 Gateway Timeout | Backend demorou mais que `proxy_read_timeout`. Suba o timeout ou otimize a aplicação |
| 413 Content Too Large | `client_max_body_size` (padrão 1m). No ingress-nginx, annotation `proxy-body-size` |
| 404 inesperado | Location errado capturando a URI (regex!), `root`/`alias` trocados. Teste com `curl -v` e confira o error_log, que mostra o caminho de arquivo tentado |
| Redirect em loop | Atrás de outro proxy/LB terminando TLS, o nginx não vê HTTPS. Respeite `X-Forwarded-Proto` antes de redirecionar |
| Configuração nova "não pega" | Faltou `nginx -t && reload`, arquivo fora do include, ou outro server block com precedência no `server_name` |

### Kit de diagnóstico

```bash
nginx -t                                # sintaxe ok?
nginx -T | grep -A5 "server_name api"   # o que está valendo de verdade
curl -v -H "Host: app.exemplo.com.br" http://127.0.0.1/saude
                                        # testa um vhost específico direto no servidor
tail -f /var/log/nginx/error.log        # a resposta quase sempre está aqui
ss -tlnp | grep nginx                   # em quais portas ele realmente escuta
```

---

## 9. Referências

- [Documentação oficial de diretivas](https://nginx.org/en/docs/) (a referência por módulo é excelente)
- Pitfalls oficiais: busque "nginx common pitfalls" na wiki do projeto
- [ingress-nginx](https://kubernetes.github.io/ingress-nginx/), em especial a página de annotations
- [Mozilla SSL Configuration Generator](https://ssl-config.mozilla.org/) para o bloco TLS
- [njs](https://nginx.org/en/docs/njs/) e [OpenResty](https://openresty.org/) para scripting
