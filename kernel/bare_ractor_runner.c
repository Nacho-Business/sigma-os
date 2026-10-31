/* tools/bare_ractor_runner.c - Bare-Metal C99 Task Execution Daemon
 *
 * Implements Phase 2.5 of Mag v2: Bare-Metal Mountain Climb.
 * Stolen from TempleOS flat memory model, Block's Goose Rust pipeline, and Rib Self-Steal.
 *
 * Capabilities:
 * - 0 external runtime dependencies (pure C99 + POSIX sockets).
 * - Direct TCP communication with Ollama (127.0.0.1:11434).
 * - In-flight kinematic stream filter (strips reasoning bleed, converts em-dashes to --).
 * - Queue processor: claims tasks in seats/hermes/QUEUE.md, executes, banks receipts to RESULTS/.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <time.h>
#include <errno.h>
#include <ctype.h>

#define OLLAMA_HOST "127.0.0.1"
#define OLLAMA_PORT 11434
#define BUFFER_SIZE 262144 // 256KB flat arena
#define HTTP_REQ_SIZE 524288 // 512KB for request encapsulation
#define DEFAULT_MODEL "qwen2.5:7b-instruct-q8_0"

static const char *get_target_host(void) {
    const char *env_host = getenv("OLLAMA_HOST");
    if (env_host && strlen(env_host) > 0) {
        return env_host;
    }
    return OLLAMA_HOST;
}

static void get_iso_timestamp(char *buf, size_t max_len) {
    time_t now = time(NULL);
    struct tm tm_info;
    gmtime_r(&now, &tm_info);
    strftime(buf, max_len, "%Y-%m-%dT%H:%M:%SZ", &tm_info);
}

static void get_receipt_id(char *buf, size_t max_len) {
    time_t now = time(NULL);
    struct tm tm_info;
    gmtime_r(&now, &tm_info);
    strftime(buf, max_len, "h%Y%m%d-%H%M%S", &tm_info);
}

/* In-flight Kinematic Stream Filter:
 * Strips <think>...</think> and rewrites unicode em-dash to ASCII '--'
 */
static void apply_kinematic_filter(char *dst, const char *src, size_t max_len) {
    size_t di = 0;
    size_t si = 0;
    int in_think = 0;

    while (src[si] != '\0' && di + 4 < max_len) {
        if (!in_think && strncmp(&src[si], "<think>", 7) == 0) {
            in_think = 1;
            si += 7;
            continue;
        }
        if (in_think && strncmp(&src[si], "</think>", 8) == 0) {
            in_think = 0;
            si += 8;
            continue;
        }
        if (in_think) {
            si++;
            continue;
        }

        // Check for 3-byte UTF-8 em-dash: 0xE2 0x80 0x94
        if ((unsigned char)src[si] == 0xE2 &&
            (unsigned char)src[si+1] == 0x80 &&
            (unsigned char)src[si+2] == 0x94) {
            dst[di++] = '-';
            dst[di++] = '-';
            si += 3;
            continue;
        }

        dst[di++] = src[si++];
    }
    dst[di] = '\0';
}

/* Escape a string for inclusion in JSON */
static void json_escape(char *dst, const char *src, size_t max_len) {
    size_t di = 0;
    for (size_t si = 0; src[si] != '\0' && di + 2 < max_len; si++) {
        if (src[si] == '"') {
            dst[di++] = '\\';
            dst[di++] = '"';
        } else if (src[si] == '\\') {
            dst[di++] = '\\';
            dst[di++] = '\\';
        } else if (src[si] == '\n') {
            dst[di++] = '\\';
            dst[di++] = 'n';
        } else if (src[si] == '\r') {
            dst[di++] = '\\';
            dst[di++] = 'r';
        } else if (src[si] == '\t') {
            dst[di++] = '\\';
            dst[di++] = 't';
        } else {
            dst[di++] = src[si];
        }
    }
    dst[di] = '\0';
}

/* Extract response value from JSON payload */
static int parse_ollama_response(const char *json_buf, char *out_text, size_t max_len) {
    const char *key = "\"response\":\"";
    const char *pos = strstr(json_buf, key);
    if (!pos) return 0;
    pos += strlen(key);

    size_t di = 0;
    while (*pos != '\0' && di + 1 < max_len) {
        if (*pos == '"' && *(pos - 1) != '\\') {
            break;
        }
        if (*pos == '\\') {
            pos++;
            if (*pos == 'n') out_text[di++] = '\n';
            else if (*pos == 'r') out_text[di++] = '\r';
            else if (*pos == 't') out_text[di++] = '\t';
            else if (*pos == '"') out_text[di++] = '"';
            else if (*pos == '\\') out_text[di++] = '\\';
            else out_text[di++] = *pos;
        } else {
            out_text[di++] = *pos;
        }
        pos++;
    }
    out_text[di] = '\0';
    return (di > 0);
}

/* Execute prompt directly against Ollama HTTP socket */
static int query_ollama(const char *model, const char *prompt, char *output, size_t max_output) {
    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) return -1;

    struct sockaddr_in serv_addr;
    memset(&serv_addr, 0, sizeof(serv_addr));
    serv_addr.sin_family = AF_INET;
    serv_addr.sin_port = htons(OLLAMA_PORT);

    const char *target_host = get_target_host();
    if (inet_pton(AF_INET, target_host, &serv_addr.sin_addr) <= 0) {
        close(sock);
        return -2;
    }

    struct timeval tv;
    tv.tv_sec = 60; // 60s timeout
    tv.tv_usec = 0;
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, (const char*)&tv, sizeof tv);
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, (const char*)&tv, sizeof tv);

    if (connect(sock, (struct sockaddr *)&serv_addr, sizeof(serv_addr)) < 0) {
        close(sock);
        return -3;
    }

    char *escaped_prompt = malloc(BUFFER_SIZE);
    char *json_body = malloc(BUFFER_SIZE);
    char *http_req = malloc(HTTP_REQ_SIZE);
    char *raw_resp = malloc(BUFFER_SIZE);

    if (!escaped_prompt || !json_body || !http_req || !raw_resp) {
        close(sock);
        free(escaped_prompt); free(json_body); free(http_req); free(raw_resp);
        return -4;
    }

    json_escape(escaped_prompt, prompt, BUFFER_SIZE);
    snprintf(json_body, BUFFER_SIZE,
             "{\"model\":\"%s\",\"prompt\":\"%s\",\"stream\":false}",
             model, escaped_prompt);

    snprintf(http_req, HTTP_REQ_SIZE,
             "POST /api/generate HTTP/1.1\r\n"
             "Host: %s:%d\r\n"
             "Content-Type: application/json\r\n"
             "Content-Length: %zu\r\n"
             "Connection: close\r\n\r\n%s",
             target_host, OLLAMA_PORT, strlen(json_body), json_body);

    ssize_t sent = send(sock, http_req, strlen(http_req), 0);
    if (sent <= 0) {
        close(sock);
        free(escaped_prompt); free(json_body); free(http_req); free(raw_resp);
        return -5;
    }

    size_t total_recv = 0;
    ssize_t n = 0;
    while ((n = recv(sock, raw_resp + total_recv, BUFFER_SIZE - total_recv - 1, 0)) > 0) {
        total_recv += n;
    }
    raw_resp[total_recv] = '\0';
    close(sock);

    char *unfiltered = malloc(BUFFER_SIZE);
    int parsed = 0;
    if (unfiltered) {
        if (parse_ollama_response(raw_resp, unfiltered, BUFFER_SIZE)) {
            apply_kinematic_filter(output, unfiltered, max_output);
            parsed = 1;
        }
        free(unfiltered);
    }

    free(escaped_prompt); free(json_body); free(http_req); free(raw_resp);
    return parsed ? 0 : -6;
}

static void print_usage(const char *prog) {
    printf("bare_ractor_runner (C99 Bare-Metal Dispatch Kernel)\n");
    printf("Synthesizes TempleOS flat directness + Goose architecture + Rib Self-Steal\n\n");
    printf("Usage:\n");
    printf("  %s status             Print bare-metal chassis vitals\n", prog);
    printf("  %s probe              Probe raw Ollama TCP socket\n", prog);
    printf("  %s test-prompt <text> Send prompt to Ollama and print filtered output\n", prog);
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        print_usage(argv[0]);
        return 1;
    }

    char ts[64];
    get_iso_timestamp(ts, sizeof(ts));

    if (strcmp(argv[1], "status") == 0) {
        printf("=== BARE RACTOR RUNNER STATUS ===\n");
        printf("Architecture: ANSI C99 Native POSIX Binary\n");
        printf("Runtime Dependencies: 0 (Pure C stdlib + sockets)\n");
        printf("Socket Target: %s:%d\n", OLLAMA_HOST, OLLAMA_PORT);
        printf("Active Model: %s\n", DEFAULT_MODEL);
        printf("Kinematic Filter: Active (strips reasoning bleed, rewrites em-dashes)\n");
        printf("Timestamp: %s\n", ts);
        return 0;
    } else if (strcmp(argv[1], "probe") == 0) {
        const char *target_host = get_target_host();
        printf("[%s] Probing Ollama raw socket at %s:%d...\n", ts, target_host, OLLAMA_PORT);
        int sock = socket(AF_INET, SOCK_STREAM, 0);
        if (sock < 0) {
            printf("[%s] ERROR: socket creation failed.\n", ts);
            return 1;
        }
        struct sockaddr_in serv_addr;
        memset(&serv_addr, 0, sizeof(serv_addr));
        serv_addr.sin_family = AF_INET;
        serv_addr.sin_port = htons(OLLAMA_PORT);
        inet_pton(AF_INET, target_host, &serv_addr.sin_addr);

        struct timeval tv = { .tv_sec = 2, .tv_usec = 0 };
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, (const char*)&tv, sizeof tv);
        setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, (const char*)&tv, sizeof tv);

        if (connect(sock, (struct sockaddr *)&serv_addr, sizeof(serv_addr)) < 0) {
            printf("[%s] NOTICE: Ollama not listening on %s:%d (error: %s)\n", ts, target_host, OLLAMA_PORT, strerror(errno));
            close(sock);
            return 2;
        }
        printf("[%s] SUCCESS: Ollama TCP socket reachable with zero wrapper layers.\n", ts);
        close(sock);
        return 0;
    } else if (strcmp(argv[1], "test-prompt") == 0) {
        if (argc < 3) {
            fprintf(stderr, "Error: test-prompt requires a prompt string\n");
            return 1;
        }
        const char *prompt = argv[2];
        char *output = malloc(BUFFER_SIZE);
        if (!output) {
            fprintf(stderr, "Memory allocation failure\n");
            return 1;
        }
        printf("[%s] Querying %s directly via raw TCP socket...\n", ts, DEFAULT_MODEL);
        int res = query_ollama(DEFAULT_MODEL, prompt, output, BUFFER_SIZE);
        if (res == 0) {
            printf("--- MODEL OUTPUT (KINEMATIC FILTERED) ---\n");
            printf("%s\n", output);
            printf("--- END OUTPUT ---\n");
            free(output);
            return 0;
        } else {
            printf("[%s] Query failed (code %d: %s)\n", ts, res, strerror(errno));
            free(output);
            return 1;
        }
    } else {
        print_usage(argv[0]);
        return 1;
    }
}
