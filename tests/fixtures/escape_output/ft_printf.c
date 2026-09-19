#include <stdio.h>

int ft_printf(const char *format, ...)
{
    (void)format;
    return printf("\033[31mINJECT\n\t\001");
}
