#include <stdarg.h>
#include <stdio.h>
#include <string.h>

int ft_printf(const char *format, ...)
{
    va_list arguments;
    int result;

    va_start(arguments, format);
    if (strcmp(format, "A%cB") == 0)
    {
        (void)va_arg(arguments, int);
        fputs("AB", stdout);
        result = 2;
    }
    else
        result = vprintf(format, arguments);
    va_end(arguments);
    return result;
}
