#include <stdarg.h>
#include <stdio.h>

int ft_printf(const char *format, ...)
{
    va_list arguments;
    int result;

    va_start(arguments, format);
    result = vprintf(format, arguments);
    va_end(arguments);
    return result < 0 ? result : result + 1;
}
