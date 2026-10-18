#!/bin/sh
# Starts (up) or removes (down) the three databases area N5 uses, on fixed ports 58601-58603.
# The password is a marker, so any output that leaks it is easy to find.
PW=N5-s3cret-marker
case "$1" in
up)
  docker run -d --name n5-pg -p 58601:5432 -e POSTGRES_USER=test -e POSTGRES_PASSWORD=$PW -e POSTGRES_DB=testdb -e TZ=UTC postgres:17-alpine -c max_connections=300
  docker run -d --name n5-mysql -p 58602:3306 -e MYSQL_ROOT_PASSWORD=$PW -e MYSQL_DATABASE=testdb -e MYSQL_USER=test -e MYSQL_PASSWORD=$PW -e TZ=UTC mysql:9.4.0
  docker run -d --name n5-mariadb -p 58603:3306 -e MARIADB_ROOT_PASSWORD=$PW -e MARIADB_DATABASE=testdb -e MARIADB_USER=test -e MARIADB_PASSWORD=$PW -e TZ=UTC mariadb:11.8
  ;;
down)
  docker rm -f n5-pg n5-mysql n5-mariadb
  ;;
esac
