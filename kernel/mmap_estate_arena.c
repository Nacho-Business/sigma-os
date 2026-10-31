/* tools/mmap_estate_arena.c - Zero-Copy Memory-Mapped State Arena
 *
 * Implements Phase 2.5.2 of Mag v2: Bare-Metal Mountain Climb.
 * Stolen from TempleOS flat memory model & high-frequency ring buffers.
 *
 * Bypasses WSL DrvFs 9p metadata latency by mapping hot state directly
 * into kernel-cached linear RAM. Zero syscalls for reads.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <errno.h>

#define ARENA_MAGIC 0x4D414732 // 'MAG2'
#define ARENA_VERSION 1
#define ARENA_SIZE (16 * 1024 * 1024) // 16MB contiguous pool
#define MAX_PHEROMONE_SLOTS 512
#define MAX_TASK_SLOTS 256
#define TEXT_LEN 256

#pragma pack(push, 1)

typedef struct {
    uint64_t timestamp;
    uint32_t task_id;
    uint8_t status; // 0=EMPTY, 1=PENDING, 2=CLAIMED, 3=DONE, 4=FAILED
    char title[TEXT_LEN];
} TaskSlot;

typedef struct {
    uint64_t timestamp;
    float weight;
    char trail[64];
    char node[32];
    char note[128];
} PheromoneSlot;

typedef struct {
    uint32_t magic;
    uint32_t version;
    uint64_t sequence_id;
    uint32_t task_head;
    uint32_t task_tail;
    uint32_t pheromone_head;
    uint32_t active_tasks_count;
    uint32_t active_pheromones_count;
    uint64_t last_sync_time;
    uint8_t reserved[192]; // Pad to 256 bytes header
    TaskSlot tasks[MAX_TASK_SLOTS];
    PheromoneSlot pheromones[MAX_PHEROMONE_SLOTS];
} EstateArenaHeader;

#pragma pack(pop)

static EstateArenaHeader *open_or_create_arena(const char *path, int *out_fd) {
    int fd = open(path, O_RDWR | O_CREAT, 0644);
    if (fd < 0) return NULL;

    struct stat st;
    if (fstat(fd, &st) < 0) {
        close(fd);
        return NULL;
    }

    if (st.st_size < ARENA_SIZE) {
        if (ftruncate(fd, ARENA_SIZE) < 0) {
            close(fd);
            return NULL;
        }
    }

    void *addr = mmap(NULL, ARENA_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (addr == MAP_FAILED) {
        close(fd);
        return NULL;
    }

    EstateArenaHeader *hdr = (EstateArenaHeader *)addr;
    if (hdr->magic != ARENA_MAGIC) {
        memset(addr, 0, ARENA_SIZE);
        hdr->magic = ARENA_MAGIC;
        hdr->version = ARENA_VERSION;
        hdr->sequence_id = 1;
        hdr->last_sync_time = (uint64_t)time(NULL);
        msync(addr, sizeof(EstateArenaHeader), MS_SYNC);
    }

    *out_fd = fd;
    return hdr;
}

static void print_usage(const char *prog) {
    printf("mmap_estate_arena (Phase 2.5.2 Zero-Copy Memory Arena)\n");
    printf("Usage:\n");
    printf("  %s init <path>                  Initialize or verify arena\n", prog);
    printf("  %s status <path>                Dump current memory-mapped vitals\n", prog);
    printf("  %s push-task <path> <id> <text> Append task directly to memory ring\n", prog);
    printf("  %s push-phero <path> <trail> <node> <weight> <note>\n", prog);
}

int main(int argc, char *argv[]) {
    if (argc < 3) {
        print_usage(argv[0]);
        return 1;
    }

    const char *action = argv[1];
    const char *arena_path = argv[2];
    int fd = -1;

    EstateArenaHeader *hdr = open_or_create_arena(arena_path, &fd);
    if (!hdr) {
        fprintf(stderr, "Error opening/mapping arena at '%s': %s\n", arena_path, strerror(errno));
        return 2;
    }

    if (strcmp(action, "init") == 0) {
        printf("[MMAP-ARENA] Initialized arena at %s\n", arena_path);
        printf("  Magic: 0x%08X (valid: %s)\n", hdr->magic, hdr->magic == ARENA_MAGIC ? "YES" : "NO");
        printf("  Version: %u\n", hdr->version);
        printf("  Sequence: %lu\n", (unsigned long)hdr->sequence_id);
        printf("  Total Size: %d MB contiguous RAM\n", ARENA_SIZE / (1024 * 1024));
    } else if (strcmp(action, "status") == 0) {
        printf("=== ESTATE MMAP ARENA VITALS ===\n");
        printf("Sequence ID: %lu\n", (unsigned long)hdr->sequence_id);
        printf("Active Tasks: %u / %d max\n", hdr->active_tasks_count, MAX_TASK_SLOTS);
        printf("Active Pheromones: %u / %d max\n", hdr->active_pheromones_count, MAX_PHEROMONE_SLOTS);
        printf("Last Sync: %lu\n", (unsigned long)hdr->last_sync_time);

        if (hdr->active_tasks_count > 0) {
            printf("\n-- RECENT TASKS IN ARENA --\n");
            uint32_t count = 0;
            for (int i = 0; i < MAX_TASK_SLOTS && count < hdr->active_tasks_count; i++) {
                if (hdr->tasks[i].status != 0) {
                    const char *st_str = "UNKNOWN";
                    if (hdr->tasks[i].status == 1) st_str = "PENDING";
                    else if (hdr->tasks[i].status == 2) st_str = "CLAIMED";
                    else if (hdr->tasks[i].status == 3) st_str = "DONE";
                    else if (hdr->tasks[i].status == 4) st_str = "FAILED";
                    printf("  [%u] Task %u: %s (%s)\n", i, hdr->tasks[i].task_id, hdr->tasks[i].title, st_str);
                    count++;
                }
            }
        }
    } else if (strcmp(action, "push-task") == 0) {
        if (argc < 5) {
            fprintf(stderr, "Error: push-task requires <task_id> <title>\n");
            munmap(hdr, ARENA_SIZE);
            close(fd);
            return 1;
        }
        uint32_t task_id = (uint32_t)atoi(argv[3]);
        const char *title = argv[4];

        uint32_t slot_idx = hdr->task_tail % MAX_TASK_SLOTS;
        hdr->tasks[slot_idx].timestamp = (uint64_t)time(NULL);
        hdr->tasks[slot_idx].task_id = task_id;
        hdr->tasks[slot_idx].status = 1; // PENDING
        strncpy(hdr->tasks[slot_idx].title, title, TEXT_LEN - 1);
        hdr->tasks[slot_idx].title[TEXT_LEN - 1] = '\0';

        hdr->task_tail++;
        hdr->active_tasks_count++;
        hdr->sequence_id++;
        hdr->last_sync_time = (uint64_t)time(NULL);

        msync(&hdr->tasks[slot_idx], sizeof(TaskSlot), MS_ASYNC);
        msync(hdr, sizeof(EstateArenaHeader), MS_ASYNC);

        printf("[MMAP-ARENA] Pushed Task %u to slot %u (seq %lu)\n", task_id, slot_idx, (unsigned long)hdr->sequence_id);
    } else if (strcmp(action, "push-phero") == 0) {
        if (argc < 7) {
            fprintf(stderr, "Error: push-phero requires <trail> <node> <weight> <note>\n");
            munmap(hdr, ARENA_SIZE);
            close(fd);
            return 1;
        }
        const char *trail = argv[3];
        const char *node = argv[4];
        float weight = (float)atof(argv[5]);
        const char *note = argv[6];

        uint32_t p_idx = hdr->pheromone_head % MAX_PHEROMONE_SLOTS;
        hdr->pheromones[p_idx].timestamp = (uint64_t)time(NULL);
        hdr->pheromones[p_idx].weight = weight;
        strncpy(hdr->pheromones[p_idx].trail, trail, 63);
        strncpy(hdr->pheromones[p_idx].node, node, 31);
        strncpy(hdr->pheromones[p_idx].note, note, 127);

        hdr->pheromone_head++;
        if (hdr->active_pheromones_count < MAX_PHEROMONE_SLOTS) {
            hdr->active_pheromones_count++;
        }
        hdr->sequence_id++;
        hdr->last_sync_time = (uint64_t)time(NULL);

        msync(&hdr->pheromones[p_idx], sizeof(PheromoneSlot), MS_ASYNC);
        msync(hdr, sizeof(EstateArenaHeader), MS_ASYNC);

        printf("[MMAP-ARENA] Deposited pheromone '%s' on node '%s' (weight %.2f, seq %lu)\n",
               trail, node, weight, (unsigned long)hdr->sequence_id);
    } else {
        print_usage(argv[0]);
    }

    munmap(hdr, ARENA_SIZE);
    close(fd);
    return 0;
}
