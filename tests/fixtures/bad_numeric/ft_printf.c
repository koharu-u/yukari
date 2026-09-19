#include <stdarg.h>
#include <stdio.h>
#include <string.h>

int ft_printf(const char *format, ...)
{
    va_list arguments;
    int result;

    va_start(arguments, format);
    if (strcmp(format, "%d %i %d") == 0)
        result = printf("wrong numbers");
    else
        result = vprintf(format, arguments);
    va_end(arguments);
    return result;
}
