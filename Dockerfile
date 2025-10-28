# Dockerfile
FROM nginx:alpine

# nginx.conf ni nginx papkasiga nusxalash
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Barcha fayllarni nginx html papkasiga nusxalash
COPY . /usr/share/nginx/html

# Port
EXPOSE 80