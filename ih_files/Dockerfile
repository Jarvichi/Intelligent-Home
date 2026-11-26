ARG BUILD_FROM=alpine:3.20
FROM ${BUILD_FROM}
# python3 runs the read-only UI page (stdlib only). fruit, streams_xattr and catia ship in samba-server (pulled in by samba); Alpine 3.20 has no separate samba-vfs-modules package. acl gives setfacl.
RUN apk add --no-cache samba samba-common-tools acl jq bash shadow python3
COPY app /app
COPY run.sh cleanup.sh /
RUN chmod 755 /run.sh /cleanup.sh
CMD ["/run.sh"]
