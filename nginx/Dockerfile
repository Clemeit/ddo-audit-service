FROM nginx:latest

# Remove the default Nginx configuration file
RUN rm -rf /usr/share/nginx/html/*

# Copy the build files from the host to the container
# COPY /client /usr/share/nginx/html

# Copy the Nginx configuration file from the host to the container
COPY ./nginx/nginx.conf /etc/nginx/nginx.conf
COPY ./nginx/api.conf /etc/nginx/conf.d/api.conf
COPY ./nginx/www.conf /etc/nginx/conf.d/www.conf
COPY ./nginx/pgadmin.conf /etc/nginx/conf.d/pgadmin.conf

RUN mkdir -p /etc/nginx/ssl