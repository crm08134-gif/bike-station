# Spring Boot API 이미지. 소스에서 jar를 빌드한 뒤 JRE만 있는 이미지로 옮긴다.
# 테스트는 Docker(Testcontainers)가 필요해 여기서는 건너뛴다 (./gradlew test 는 따로 실행).

FROM eclipse-temurin:25-jdk AS build
WORKDIR /src
# 의존성만 먼저 받아 두면 소스만 바뀔 때 이 층을 다시 쓴다
COPY gradlew settings.gradle.kts build.gradle.kts ./
COPY gradle gradle
RUN chmod +x gradlew && ./gradlew --no-daemon -q dependencies > /dev/null
COPY src src
RUN ./gradlew --no-daemon bootJar \
 && find build/libs -name '*.jar' ! -name '*-plain.jar' -exec cp {} /app.jar \;

FROM eclipse-temurin:25-jre
RUN useradd --system --no-create-home app
USER app
COPY --from=build /app.jar /app/app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
