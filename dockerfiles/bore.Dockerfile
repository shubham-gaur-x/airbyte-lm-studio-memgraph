FROM alpine:3.19 AS builder
RUN apk add --no-cache curl tar
RUN curl -L -o /tmp/bore.tar.gz \
    "https://github.com/ekzhang/bore/releases/download/v0.6.0/bore-v0.6.0-aarch64-unknown-linux-musl.tar.gz" \
    && tar xzf /tmp/bore.tar.gz -C /usr/local/bin/ \
    && chmod +x /usr/local/bin/bore

FROM alpine:3.19
COPY --from=builder /usr/local/bin/bore /usr/local/bin/bore
ENTRYPOINT ["bore"]
