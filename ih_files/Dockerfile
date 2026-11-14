ARG BUILD_FROM=alpine:3.20
FROM ${BUILD_FROM}
# samba-vfs-modules holds fruit, streams_xattr and catia (without it smbd will not start); acl gives setfacl.
RUN apk add --no-cache samba samba-common-tools samba-vfs-modules acl jq bash shadow
COPY run.sh cleanup.sh /
RUN chmod 755 /run.sh /cleanup.sh
CMD ["/run.sh"]
