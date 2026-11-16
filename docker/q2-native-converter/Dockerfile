# The build context must be the explicit safe-file bundle, never a private
# deployment snapshot or a checkout containing credentials/model weights.
FROM python:3.12-slim@sha256:05a994d2344eb364cc186910f4fc8786a38215f39f00d0acc25bff33a08f4177

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
 && apt-get install --no-install-recommends -y git ca-certificates \
 && python -m pip install --no-cache-dir \
      numpy==2.3.4 ml_dtypes==0.5.4 PyYAML==6.0.3 tqdm==4.67.1 sentencepiece==0.2.1 \
 && python -m pip install --no-cache-dir --no-deps \
      'gguf @ git+https://github.com/ggml-org/llama.cpp.git@c49edf0963a76d5e78b2a7278c9bd5b71d26c84f#subdirectory=gguf-py' \
 && apt-get purge -y git \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* /root/.cache

WORKDIR /app
COPY reconstruct-q2-native.py q2-native-quant.py plan-q2-native-overlay.py ./
COPY q2-native-compact-plan.json ./
COPY test-q2-native-quant.py test-q2-native-reconstruction.py ./
RUN python test-q2-native-quant.py && python test-q2-native-reconstruction.py \
 && rm test-q2-native-quant.py test-q2-native-reconstruction.py

ENTRYPOINT ["python", "-B", "/app/reconstruct-q2-native.py"]
CMD ["--help"]
