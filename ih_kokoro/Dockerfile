FROM python:3.12-slim
# Pinned: Python packages by version, model files by sha256 (Kokoro v1.0 int8 model, about 92 MB, and the voice pack, about 28 MB; the model-files-v1.0 release of kokoro-onnx).
RUN pip install --no-cache-dir "kokoro-onnx==0.6.1" "wyoming==1.11.0"
ARG BASE=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /models \
 && curl -fsSL -o /models/kokoro.onnx "$BASE/kokoro-v1.0.int8.onnx" \
 && curl -fsSL -o /models/voices.bin "$BASE/voices-v1.0.bin" \
 && echo "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb  /models/kokoro.onnx" | sha256sum -c - \
 && echo "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d  /models/voices.bin" | sha256sum -c -
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
