FROM node:22-bookworm-slim

# Copy Java 21 JRE from Eclipse Temurin
COPY --from=eclipse-temurin:21-jre-noble /opt/java/openjdk /opt/java/openjdk

# Environment paths
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH="${JAVA_HOME}/bin:${PATH}"
ENV FIREBASE_EMULATORS_PATH=/opt/firebase/emulators

# Install curl for Docker healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install pinned firebase-tools
RUN npm install -g firebase-tools@15.32.1

# Verify Java and Firebase CLI installation at build time
RUN java -version && firebase --version

# Pre-download Firestore, Storage, and UI emulators at build time
RUN firebase setup:emulators:firestore && \
    firebase setup:emulators:storage && \
    firebase setup:emulators:ui

# Working directory for Firebase configuration
WORKDIR /srv/firebase
COPY firebase.json firestore.rules storage.rules firestore.indexes.json ./

# Entrypoint setup with CRLF fix for Windows hosts
COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

EXPOSE 4000 8080 9099 9150 9199 4400 4500

ENTRYPOINT ["/entrypoint.sh"]
