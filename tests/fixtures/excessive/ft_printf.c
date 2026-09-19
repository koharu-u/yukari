#include <unistd.h>

int ft_printf(const char *format, ...)
{
    static const char block[4096] = { ['\0' ... 4095] = 'X' };
    (void)format;
    while (1)
        if (write(STDOUT_FILENO, block, sizeof(block)) < 0)
            return -1;
}
