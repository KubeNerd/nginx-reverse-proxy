```conf
# Configuração dos dados de conexão, o valor default é 1024 mesmo.
# Ele determina a quantidade de conexões simultâneas que os workers suportam.

events {
    worker_connections 1024; 
}

# Configuração de tráfego de rede.
# Estamos trabalhando com HTTP. Porém, temos também mail e stream (UDP, TCP).

http {
    # Virtual server, aqui vão os servidores virtuais e suas configurações de redirecionamento.
    # Cada bloco server representa um servidor.

    server {
        
        # O listen determina em qual porta iremos escutar, e posteriormente redirecionar.
        listen 80;
        
        # Nome do servidor ou domínio que iremos utilizar.
        server_name 127.0.0.1;
        
    }
        location /green_app {
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_pass http://yellow_app:80;
        }
        location /yellow_app {
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_pass http://green_app:80;   
        }
}



```