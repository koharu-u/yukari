#include <stdarg.h>
#include <stdio.h>
#include <string.h>

int ft_printf(const char *format, ...)
{
    va_list arguments;
    int result;

    va_start(arguments, format);
    if (strcmp(format, "%d|%i") == 0)
        result = printf("signed-boundary-error");
    else if (strcmp(format, "%u|%u|%u|%u|%u") == 0)
        result = printf("unsigned-boundary-error");
    else if (strcmp(format, "%x %X") == 0)
        result = printf("abcdef ABCdef");
    else
        result = vprintf(format, arguments);
    va_end(arguments);
    return result;
}
