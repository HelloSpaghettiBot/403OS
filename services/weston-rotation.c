/* SPDX-License-Identifier: MIT
 * Private, bounded display transforms on Weston's compositor event loop.
 * No client, compositor or physical DRM mode is restarted or replaced.
 */
#define _GNU_SOURCE
#include <libweston/libweston.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#define ROTATION_DIRECTORY "/run/surface/display"
#define ROTATION_SOCKET ROTATION_DIRECTORY "/rotation.sock"
#define MAX_CLIENTS 4

struct rotation_server;
struct rotation_client {
    struct rotation_server *server;
    struct wl_event_source *source, *timer;
    int fd;
};
struct rotation_server {
    struct weston_compositor *compositor;
    struct wl_event_source *source;
    struct wl_listener destroy;
    struct rotation_client clients[MAX_CLIENTS];
    int fd;
    int disposable_vm;
};

static int
parse_request(char *packet, size_t size, char **name, uint32_t *transform)
{
    char *space;
    if (!size || size > 80 || memchr(packet, '\0', size))
        return -1;
    for (size_t i = 0; i < size; ++i)
        if (!((packet[i] >= 'A' && packet[i] <= 'Z') ||
              (packet[i] >= 'a' && packet[i] <= 'z') ||
              (packet[i] >= '0' && packet[i] <= '9') ||
              packet[i] == '-' || packet[i] == ' '))
            return -1;
    packet[size] = '\0';
    space = strchr(packet, ' ');
    if (!space || space == packet || space - packet > 63 || strchr(space + 1, ' '))
        return -1;
    *space++ = '\0';
    if (!strcmp(space, "normal")) *transform = WL_OUTPUT_TRANSFORM_NORMAL;
    else if (!strcmp(space, "90")) *transform = WL_OUTPUT_TRANSFORM_90;
    else if (!strcmp(space, "180")) *transform = WL_OUTPUT_TRANSFORM_180;
    else if (!strcmp(space, "270")) *transform = WL_OUTPUT_TRANSFORM_270;
    else return -1;
    *name = packet;
    return 0;
}

static void
close_client(struct rotation_client *client)
{
    if (client->source) wl_event_source_remove(client->source);
    if (client->timer) wl_event_source_remove(client->timer);
    client->source = client->timer = NULL;
    if (client->fd >= 0) close(client->fd);
    client->fd = -1;
}

static int
expire_client(void *data)
{
    close_client(data);
    return 0;
}

static int
read_client(int fd, uint32_t mask, void *data)
{
    struct rotation_client *client = data;
    struct weston_output *output;
    uint32_t transform;
    char packet[128], *name;
    const char *reply = "ERROR invalid rotation request";
    ssize_t size;
    (void) mask;
    size = recv(fd, packet, sizeof packet - 1, MSG_DONTWAIT | MSG_TRUNC);
    if (size < 0 && (errno == EAGAIN || errno == EINTR)) return 0;
    if (size > 0 && size < (ssize_t) sizeof packet &&
        !parse_request(packet, (size_t) size, &name, &transform) &&
        (client->server->disposable_vm || !strncmp(name, "eDP-", 4) ||
         !strncmp(name, "DSI-", 4) || !strncmp(name, "LVDS-", 5))) {
        reply = "ERROR internal display is unavailable";
        wl_list_for_each(output, &client->server->compositor->output_list, link) {
            if (!output->enabled || !output->name || strcmp(output->name, name)) continue;
            if (output->transform != transform) {
                weston_output_set_transform(output, transform);
                /* Weston 15's transform API updates output geometry but does
                 * not emit this signal; kiosk-shell uses it to resize live views. */
                wl_signal_emit(&output->compositor->output_resized_signal, output);
                weston_compositor_damage_all(output->compositor);
            }
            reply = "OK";
            break;
        }
    }
    (void) send(fd, reply, strlen(reply), MSG_DONTWAIT | MSG_NOSIGNAL);
    close_client(client);
    return 0;
}

static int
accept_clients(int fd, uint32_t mask, void *data)
{
    struct rotation_server *server = data;
    struct wl_event_loop *loop = wl_display_get_event_loop(server->compositor->wl_display);
    (void) mask;
    for (int attempt = 0; attempt < MAX_CLIENTS; ++attempt) {
        struct ucred peer;
        socklen_t length = sizeof peer;
        struct rotation_client *client = NULL;
        int incoming = accept4(fd, NULL, NULL, SOCK_NONBLOCK | SOCK_CLOEXEC);
        if (incoming < 0) break;
        for (int i = 0; i < MAX_CLIENTS; ++i)
            if (server->clients[i].fd < 0) { client = &server->clients[i]; break; }
        if (!client || getsockopt(incoming, SOL_SOCKET, SO_PEERCRED, &peer, &length) || peer.uid != 0) {
            close(incoming);
            continue;
        }
        client->fd = incoming;
        client->source = wl_event_loop_add_fd(loop, incoming, WL_EVENT_READABLE, read_client, client);
        client->timer = wl_event_loop_add_timer(loop, expire_client, client);
        if (!client->source || !client->timer) close_client(client);
        else wl_event_source_timer_update(client->timer, 500);
    }
    return 0;
}

static void
destroy_server(struct wl_listener *listener, void *data)
{
    struct rotation_server *server = wl_container_of(listener, server, destroy);
    (void) data;
    for (int i = 0; i < MAX_CLIENTS; ++i) close_client(&server->clients[i]);
    if (server->source) wl_event_source_remove(server->source);
    if (server->fd >= 0) close(server->fd);
    unlink(ROTATION_SOCKET);
    wl_list_remove(&server->destroy.link);
    free(server);
}

WL_EXPORT int
wet_module_init(struct weston_compositor *compositor, int *argc, char *argv[])
{
    struct stat info;
    struct sockaddr_un address = { .sun_family = AF_UNIX };
    struct rotation_server *server;
    int marker;
    char identity[64];
    ssize_t size;
    (void) argc; (void) argv;
    /* Do not create any host state if a developer loads this module elsewhere. */
    marker = open("/etc/surface-image-id", O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (marker < 0) return 0;
    size = read(marker, identity, sizeof identity);
    int valid = !fstat(marker, &info) && S_ISREG(info.st_mode) && info.st_uid == 0 &&
                !(info.st_mode & 022) && size >= 11 && !memcmp(identity, "surface-go-", 11);
    close(marker);
    if (geteuid() != 0 || !valid) return 0;
    if (lstat(ROTATION_DIRECTORY, &info) || !S_ISDIR(info.st_mode) || info.st_uid || (info.st_mode & 077))
        return -1;
    if (!lstat(ROTATION_SOCKET, &info)) {
        if (!S_ISSOCK(info.st_mode) || info.st_uid || unlink(ROTATION_SOCKET)) return -1;
    } else if (errno != ENOENT) return -1;
    server = calloc(1, sizeof *server);
    if (!server) return -1;
    server->compositor = compositor;
    /* Actual virtual-output acceptance uses the same transform path. Never
     * grant that broader output eligibility on a physical Surface. */
    int dmi = open("/sys/class/dmi/id/product_name", O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (dmi >= 0) {
        char product[128];
        ssize_t count = read(dmi, product, sizeof product - 1);
        close(dmi);
        if (count > 0) {
            if (product[count - 1] == '\n') --count;
            product[count] = '\0';
            server->disposable_vm = count == (ssize_t) sizeof("Surface Go OS Disposable Test VM") - 1 &&
                !memcmp(product, "Surface Go OS Disposable Test VM", (size_t) count);
        }
    }
    for (int i = 0; i < MAX_CLIENTS; ++i) {
        server->clients[i].server = server;
        server->clients[i].fd = -1;
    }
    server->fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    strcpy(address.sun_path, ROTATION_SOCKET);
    mode_t previous = umask(077);
    int bound = server->fd >= 0 ? bind(server->fd, (struct sockaddr *) &address, sizeof address) : -1;
    umask(previous);
    if (bound || listen(server->fd, MAX_CLIENTS)) goto error;
    server->source = wl_event_loop_add_fd(wl_display_get_event_loop(compositor->wl_display),
                                         server->fd, WL_EVENT_READABLE, accept_clients, server);
    if (!server->source) goto error;
    server->destroy.notify = destroy_server;
    wl_signal_add(&compositor->destroy_signal, &server->destroy);
    return 0;
error:
    if (server->fd >= 0) close(server->fd);
    if (!bound) unlink(ROTATION_SOCKET);
    free(server);
    return -1;
}
