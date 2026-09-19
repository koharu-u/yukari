#include <unistd.h>

int ft_printf(const char *format, ...)
{
    (void)format;
    while (1)
        pause();
    return 0;
}
