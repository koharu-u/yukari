#include <signal.h>

int ft_printf(const char *format, ...)
{
    (void)format;
    raise(SIGSEGV);
    return 0;
}
