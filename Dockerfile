# Dockerfile
FROM nginx:alpine

# Nginx config faylini nusxalash
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Statik fayllarni nginx papkasiga nusxalash
COPY . /usr/share/nginx/html

# Port
EXPOSE 80